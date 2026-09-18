# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""In-process rank-barrier dispatcher with durable checkpoints and deadlines."""
from __future__ import annotations

import importlib.util
from importlib.abc import MetaPathFinder
from importlib.machinery import PathFinder, SourceFileLoader
import signal
import sqlite3
import tempfile
import time
import threading
import sys
from uuid import uuid4
from contextlib import contextmanager
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from types import CodeType

from ..plugin_configuration import NamespaceWrites, apply_config_batch, read_plugin_configuration, recover_config_transaction

from .api import (
    ApplicationContext, Emission, InvocationResponse, Pipeline, PluginSpec, ProcessingObject,
    MIME_TYPE, ProcessingResult, PromotedMessage, RAW_MESSAGE, RunReport,
)
from .registry import fingerprint, subscribers
from .store import content_reference, enqueue, report, snapshot


class ProcessorServices(Protocol):
    def prepare(self, item: ProcessingObject) -> ProcessingObject: ...
    def publish(self, database: sqlite3.Connection, job_id: int, item: ProcessingObject,
                plugin: PluginSpec, result: ProcessingResult) -> None: ...
    def complete(self, item: ProcessingObject) -> None: ...


class CurrentSourceLoader(SourceFileLoader):
    def get_code(self, fullname: str) -> CodeType:
        # Content fingerprints must not dispatch stale same-size/same-second pyc files.
        path = self.get_filename(fullname)
        return compile(self.get_data(path), path, "exec", dont_inherit=True)


class PluginImports(MetaPathFinder):
    def __init__(self, namespace: str) -> None:
        self.namespace = namespace

    def find_spec(self, fullname, path, target=None):
        if not fullname.startswith(self.namespace + "."):
            return None
        spec = PathFinder.find_spec(fullname, path)
        if spec is not None and isinstance(spec.loader, SourceFileLoader) and spec.origin is not None:
            spec.loader = CurrentSourceLoader(fullname, spec.origin)
        return spec


@contextmanager
def loaded_plugin(plugin: PluginSpec):
    """Give plugin-local models and relative imports a real, isolated module namespace."""
    module_name = plugin.manifest.entrypoint.split(":")[0]
    namespace = "_mailarchiver_processor_" + uuid4().hex
    path = plugin.directory / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(namespace, path, loader=CurrentSourceLoader(namespace, str(path)),
                                                 submodule_search_locations=[str(plugin.directory)])
    if spec is None or spec.loader is None:
        raise ValueError("invalid entrypoint")
    module = importlib.util.module_from_spec(spec)
    sys.modules[namespace] = module
    finder = PluginImports(namespace)
    sys.meta_path.insert(0, finder)
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.meta_path.remove(finder)
        for name in tuple(sys.modules):
            if name == namespace or name.startswith(namespace + "."):
                sys.modules.pop(name, None)


def invoke(plugin: PluginSpec, item: ProcessingObject, installation_config: Path | None = None,
           cancelled: Callable[[], None] | None = None) -> InvocationResponse:
    """Invoke trusted Python code in this interpreter; only accepted results publish."""
    configuration = read_plugin_configuration(item.archive.path, plugin.manifest.kind, installation_config)
    with tempfile.TemporaryDirectory(prefix="processor-") as directory:
        item = item.model_copy(update={"application": ApplicationContext(policy=item.application.policy, workspace=Path(directory))})
        item.bind_configuration(configuration)
        item.bind_deadline(time.monotonic() + plugin.manifest.timeout_seconds, cancelled)
        try:
            factory_name = plugin.manifest.entrypoint.split(":")[1]
            with invocation_deadline(item), loaded_plugin(plugin) as module:
                processor = getattr(module, factory_name)()
                result = processor.process(item)
                item.check_cancelled()
            if not isinstance(result, ProcessingResult):
                raise TypeError("processor must return ProcessingResult")
            validate_result(plugin, item, result)
            # Retain outputs before releasing this invocation's workspace.
            return InvocationResponse(result=result.model_copy(update={
                "config_writes": configuration.pending_writes(),
                "emissions": tuple(Emission(content_ref=snapshot(item.archive.path, emitted.content_ref.path),
                    content_type=emitted.content_type, part_path=emitted.part_path, scope=emitted.scope,
                    synthetic=emitted.synthetic, metadata=emitted.metadata) for emitted in result.emissions),
                "promotions": tuple(PromotedMessage(content_ref=snapshot(item.archive.path, child.content_ref.path),
                    part_path=child.part_path) for child in result.promotions),
            }))
        except Exception as error:
            return InvocationResponse.failure(error)


@contextmanager
def invocation_deadline(item: ProcessingObject):
    """POSIX main-thread alarm supplements the portable cooperative deadline API."""
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "setitimer"):
        yield
        return
    previous = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()
    remaining = item.remaining_seconds
    def expired(_signal, _frame):
        raise TimeoutError("processor timeout")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if previous_timer[0]:
            signal.setitimer(signal.ITIMER_REAL, max(0.001, previous_timer[0] - (time.monotonic() - started)), previous_timer[1])


def validate_result(plugin: PluginSpec, item: ProcessingObject, result: ProcessingResult) -> None:
    for emitted in result.emissions:
        dynamic_mime = (plugin.manifest.emits_mime_parts and MIME_TYPE.fullmatch(emitted.content_type)
                        and not emitted.content_type.startswith("application/x-mailarchiver-"))
        if emitted.content_type not in plugin.manifest.emits and not dynamic_mime:
            raise ValueError("undeclared emitted type")
        if emitted.part_path[:len(item.part_path)] != item.part_path:
            raise ValueError("emitted part must descend from its input")
        if content_reference(emitted.content_ref.path).sha256 != emitted.content_ref.sha256:
            raise ValueError("emitted content digest mismatch")
        if item.synthetic and not emitted.synthetic:
            raise ValueError("derived content must retain synthetic provenance")
    for handoff in result.handoffs:
        if (item.pipeline, handoff.pipeline) not in (("ingest", "message"), ("message", "content")):
            raise ValueError("same-message handoff must advance its pipeline")
    for child in result.promotions:
        if item.pipeline != "content" or child.part_path[:len(item.part_path)] != item.part_path:
            raise ValueError("child messages must descend from content processing")
        if content_reference(child.content_ref.path).sha256 != child.content_ref.sha256:
            raise ValueError("child message digest mismatch")


def _checkpoint(database: sqlite3.Connection, job_id: int, plugin: PluginSpec, item: ProcessingObject,
                installation_config: Path | None, cancelled: Callable[[], None] | None,
                failures: list[Exception], services: ProcessorServices | None) -> ProcessingResult | None:
    previous = database.execute(
        "SELECT result_json FROM invocations WHERE job_id=? AND kind=? AND status='completed' ORDER BY invocation_id DESC LIMIT 1",
        (job_id, plugin.manifest.kind)).fetchone()
    if previous:
        return ProcessingResult.model_validate_json(previous[0])
    with database:
        cursor = database.execute("INSERT INTO invocations(job_id,kind,version,rank,status) VALUES(?,?,?,?,'running')",
                                  (job_id, plugin.manifest.kind, plugin.manifest.implementation_version, plugin.manifest.rank))
        invocation_id = cursor.lastrowid
    started = time.monotonic()
    try:
        response = invoke(plugin, item, installation_config, cancelled)
        if response.result is not None:
            validate_result(plugin, item, response.result)
    except KeyboardInterrupt:
        with database:
            database.execute("UPDATE invocations SET status='failed',elapsed=?,error='cancelled' WHERE invocation_id=?",
                             (time.monotonic() - started, invocation_id))
        raise
    except Exception as error:
        response = InvocationResponse.failure(error)
    if response.cause is not None:
        failures.append(response.cause)
    elapsed = time.monotonic() - started
    success = response.result is not None and response.error is None
    with database:
        database.execute("UPDATE invocations SET status=?,elapsed=?,result_json=?,error=? WHERE invocation_id=?",
                         ("completed" if success else "failed", elapsed,
                          response.result.model_dump_json() if response.result else None,
                          response.error, invocation_id))
        if not success and response.result is not None and response.result.scan is not None and services is not None:
            services.publish(database, job_id, item, plugin, ProcessingResult(scan=response.result.scan))
    return response.result if success else None


def _publish(database: sqlite3.Connection, item: ProcessingObject, plugin: PluginSpec,
             result: ProcessingResult, registry_hash: str, job_id: int, services: ProcessorServices | None) -> None:
    if services is not None:
        services.publish(database, job_id, item, plugin, result)
    for emitted in result.emissions:
        derived = ProcessingObject(
            application=item.application, archive=item.archive, message_id=item.message_id,
            message_ref=item.message_ref, content_ref=snapshot(item.archive.path, emitted.content_ref.path), content_type=emitted.content_type,
            pipeline=item.pipeline, part_path=emitted.part_path, scope=emitted.scope,
            synthetic=emitted.synthetic, parent_message_id=item.parent_message_id,
            scan_provenance=item.scan_provenance, producer=f"{plugin.manifest.kind}:{plugin.manifest.implementation_version}",
            source_metadata=item.source_metadata, content_metadata=emitted.metadata, generation=item.generation)
        enqueue(database, derived, registry_hash, job_id)
    for handoff in result.handoffs:
        following = ProcessingObject(
            application=item.application, archive=item.archive, message_id=item.message_id,
            message_ref=item.message_ref, content_ref=item.message_ref, content_type=RAW_MESSAGE,
            pipeline=handoff.pipeline, parent_message_id=item.parent_message_id,
            scan_provenance=item.scan_provenance, source_metadata=item.source_metadata,
            content_metadata=item.content_metadata, generation=item.generation)
        enqueue(database, following, registry_hash, job_id)


def _abort(database: sqlite3.Connection, item: ProcessingObject, whole_message: bool) -> None:
    for job_id, payload in database.execute("SELECT job_id,item_json FROM jobs WHERE status IN ('pending','running')").fetchall():
        candidate = ProcessingObject.model_validate_json(payload)
        if candidate.message_id == item.message_id and (whole_message or candidate.part_path[:len(item.part_path)] == item.part_path):
            database.execute("UPDATE jobs SET status='aborted' WHERE job_id=?", (job_id,))


def run(database: sqlite3.Connection, plugins: tuple[PluginSpec, ...], *, retry: bool = False,
        max_jobs: int | None = None, installation_config: Path | None = None,
        pipelines: tuple[Pipeline, ...] = ("ingest", "message", "content"),
        services: ProcessorServices | None = None, cancelled: Callable[[], None] | None = None,
        failures: list[Exception] | None = None) -> RunReport:
    """Caller holds the archive writer lease. Atomic output release follows each barrier."""
    registry_hash = fingerprint(plugins)
    failures = failures if failures is not None else []
    incompatible = database.execute("SELECT 1 FROM jobs WHERE registry_hash<>? AND status IN ('pending','running','failed') LIMIT 1", (registry_hash,)).fetchone()
    if incompatible:
        raise ValueError("registry changed; use the explicit reprocess command")
    with database:
        database.execute("UPDATE invocations SET status='failed',error='interrupted worker' WHERE status='running'")
        database.execute("UPDATE jobs SET status='pending' WHERE status='running'")
        if retry:
            database.execute("UPDATE jobs SET status='pending',detail='' WHERE status='failed'")
    processed = 0
    while max_jobs is None or processed < max_jobs:
        placeholders = ",".join("?" for _ in pipelines)
        row = database.execute("SELECT job_id,item_json FROM jobs WHERE status='pending' "
            f"AND json_extract(item_json,'$.pipeline') IN ({placeholders}) "
            "AND (parent_job_id IS NULL OR parent_job_id IN (SELECT job_id FROM jobs WHERE status='completed')) ORDER BY job_id LIMIT 1", pipelines).fetchone()
        if row is None:
            break
        job_id, payload = row
        item = ProcessingObject.model_validate_json(payload)
        if services is not None:
            item = services.prepare(item)
        recover_config_transaction(item.archive.path)
        try:
            input_error = "input digest mismatch" if any(
                content_reference(ref.path).sha256 != ref.sha256 for ref in (item.message_ref, item.content_ref)) else None
        except OSError as error:
            input_error = f"input unreadable: {error}"
        if input_error:
            with database:
                database.execute("UPDATE jobs SET status='failed',detail=? WHERE job_id=?", (input_error, job_id))
            break
        selected = subscribers(plugins, item)
        with database:
            database.execute("UPDATE jobs SET status='running' WHERE job_id=?", (job_id,))
        stop_import = False
        aborted = False
        for rank in sorted({p.manifest.rank for p in selected}):
            if services is not None:
                item = services.prepare(item)
            results: list[tuple[PluginSpec, ProcessingResult]] = []
            failed = False
            for plugin in (p for p in selected if p.manifest.rank == rank):
                result = _checkpoint(database, job_id, plugin, item, installation_config, cancelled, failures, services)
                if result is None:
                    failed = True
                else:
                    results.append((plugin, result))
            outcomes = [result.outcome for _, result in results]
            with database:
                if failed or "fail-import" in outcomes:
                    database.execute("UPDATE jobs SET status='failed',detail='rank failed; inspect invocations' WHERE job_id=?", (job_id,))
                    stop_import = True
                elif "abort-message" in outcomes or "abort-part" in outcomes:
                    if services is not None:
                        for plugin, result in results:
                            services.publish(database, job_id, item, plugin, result)
                    _abort(database, item, "abort-message" in outcomes)
                    aborted = True
                else:
                    try:
                        apply_config_batch(item.archive.path, tuple(NamespaceWrites(
                            name=plugin.manifest.kind, writes=result.config_writes) for plugin, result in results), installation_config)
                    except (OSError, ValueError) as error:
                        if isinstance(error, ValueError):
                            for plugin, _result in results:
                                database.execute("UPDATE invocations SET status='failed',error=? WHERE job_id=? AND kind=? AND status='completed'",
                                                 (str(error), job_id, plugin.manifest.kind))
                        database.execute("UPDATE jobs SET status='failed',detail=? WHERE job_id=?", (str(error), job_id))
                        stop_import = True
                    else:
                        for plugin, result in results:
                            _publish(database, item, plugin, result, registry_hash, job_id, services)
            if stop_import or aborted:
                break
        if stop_import:
            break
        if not aborted:
            if services is not None:
                services.complete(item)
            with database:
                database.execute("UPDATE jobs SET status='completed' WHERE job_id=?", (job_id,))
        processed += 1
    return report(database, tuple(p.manifest.kind for p in plugins))
