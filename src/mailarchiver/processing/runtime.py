# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""In-process rank-barrier dispatcher with durable checkpoints and deadlines.

Purpose: Dispatch processor jobs with durable rank barriers and cooperative deadlines.

How it works:
Stream independent job generators through bounded future workers. The coordinator
validates returned results and commits successful rank outcomes/checkpoints.

Fundamental dependents:

- ``processing.production``: archive pipelines rely on this dispatcher.
- ``processing.__main__``: the standalone harness exercises the same retry/cancellation rules.
"""
from __future__ import annotations

import importlib.util
from importlib.abc import MetaPathFinder
from importlib.machinery import PathFinder, SourceFileLoader
import signal
import sqlite3
import shutil
import tempfile
import time
import threading
import sys
from uuid import uuid4
from contextlib import contextmanager, nullcontext
from collections.abc import Callable, Generator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Protocol
from types import CodeType

from pydantic import BaseModel, ConfigDict, InstanceOf

from ..plugin_configuration import PluginConfiguration, NamespaceWrites, apply_config_batch, read_plugin_configuration, recover_config_transaction

from .api import (
    ApplicationContext, InvocationResponse, Pipeline, PluginSpec, ProcessingObject,
    MIME_TYPE, ProcessingResult, RAW_MESSAGE, RunReport,
)
from .registry import fingerprint, subscribers
from .store import content_reference, enqueue, report, snapshot
from .objects import collect_objects, index_outputs

RANK_FAILURE = "rank failed; inspect invocations"
ProcessingProgress = Callable[[ProcessingObject, str, bool, int], None]


class InvocationRequest(BaseModel):
    plugin: PluginSpec
    item: ProcessingObject
    configuration: PluginConfiguration


class ActiveJob(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    message_id: str
    worker: int
    steps: InstanceOf[Generator[InvocationRequest, InvocationResponse, bool]]
    future: Future[InvocationResponse] | None = None
    workspace: Path | None = None


class ProcessorServices(Protocol):
    def prepare(self, item: ProcessingObject) -> ProcessingObject: ...
    def publish(self, database: sqlite3.Connection, job_id: int, item: ProcessingObject,
                plugin: PluginSpec, result: ProcessingResult) -> None: ...
    def complete(self, item: ProcessingObject) -> None: ...
    def release(self, message_id: str) -> None: ...
    def failed(self, item: ProcessingObject, plugin: PluginSpec, response: InvocationResponse) -> ProcessingResult | None: ...


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


def invoke(request: InvocationRequest, workspace: Path,
           cancelled: Callable[[], None] | None = None) -> InvocationResponse:
    """Run trusted code in an isolated workspace; the coordinator publishes returned results."""
    plugin, configuration = request.plugin, request.configuration
    item = request.item.model_copy(update={"application": ApplicationContext(policy=request.item.application.policy, workspace=workspace)})
    item.bind_configuration(configuration)
    item.bind_deadline(time.monotonic() + plugin.manifest.timeout_seconds, cancelled)
    result = None
    try:
        factory_name = plugin.manifest.entrypoint.split(":")[1]
        with invocation_deadline(item), loaded_plugin(plugin) as module:
            processor = getattr(module, factory_name)()
            result = processor.process(item)
            item.check_cancelled()
        if not isinstance(result, ProcessingResult):
            raise TypeError("processor must return ProcessingResult")
        validate_result(plugin, item, result)
        return InvocationResponse(result=result.model_copy(update={"config_writes": configuration.pending_writes()}))
    except Exception as error:
        response = InvocationResponse.failure(error)
        if isinstance(result, ProcessingResult) and result.scan is not None:
            response = response.model_copy(update={"result": ProcessingResult(scan=result.scan)})
        return response


@contextmanager
def invocation_deadline(item: ProcessingObject):
    """POSIX main-thread alarm supplements the portable cooperative deadline API."""
    if sys.platform == "win32" or threading.current_thread() is not threading.main_thread() or not hasattr(signal, "setitimer"):
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
                installation_config: Path | None, failures: list[Exception],
                services: ProcessorServices | None) -> Generator[InvocationRequest, InvocationResponse, ProcessingResult | None]:
    previous = database.execute(
        "SELECT coalesce(a.result_json,i.result_json) FROM invocations i "
        "LEFT JOIN accepted_invocations a USING(invocation_id) "
        "WHERE i.job_id=? AND i.kind=? AND (i.status='completed' OR a.invocation_id IS NOT NULL) "
        "ORDER BY i.invocation_id DESC LIMIT 1",
        (job_id, plugin.manifest.kind)).fetchone()
    if previous:
        return ProcessingResult.model_validate_json(previous[0])
    with database:
        cursor = database.execute("INSERT INTO invocations(job_id,kind,version,rank,status) VALUES(?,?,?,?,'running')",
                                  (job_id, plugin.manifest.kind, plugin.manifest.implementation_version, plugin.manifest.rank))
        invocation_id = cursor.lastrowid
    started = time.monotonic()
    try:
        response = yield InvocationRequest(plugin=plugin, item=item,
            configuration=read_plugin_configuration(item.archive.path, plugin.manifest.kind, installation_config))
        if response.result is not None:
            validate_result(plugin, item, response.result)
            # Retain outputs on the coordinator before indexing them or releasing the workspace.
            response = response.model_copy(update={"result": response.result.model_copy(update={
                "emissions": tuple(emitted.model_copy(update={"content_ref": snapshot(item.archive.path, emitted.content_ref.path)})
                                   for emitted in response.result.emissions),
                "promotions": tuple(child.model_copy(update={"content_ref": snapshot(item.archive.path, child.content_ref.path)})
                                    for child in response.result.promotions),
            })})
    except (KeyboardInterrupt, GeneratorExit):
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
    accepted = services.failed(item, plugin, response) if not success and services is not None else None
    with database:
        database.execute("UPDATE invocations SET status=?,elapsed=?,result_json=?,error=? WHERE invocation_id=?",
                         ("completed" if success else "failed", elapsed,
                          response.result.model_dump_json() if success and response.result else response.model_dump_json(),
                          response.error, invocation_id))
        if success and response.result is not None:
            index_outputs(database, job_id, item.message_ref.sha256, response.result)
        if accepted is not None:
            database.execute("INSERT INTO accepted_invocations VALUES(?,?)", (invocation_id, accepted.model_dump_json()))
        if not success and response.result is not None and response.result.scan is not None and services is not None:
            services.publish(database, job_id, item, plugin, ProcessingResult(scan=response.result.scan))
    if not success:
        return accepted
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
        failures: list[Exception] | None = None, message_id: str | None = None,
        continue_after_failure: bool = False, progress: ProcessingProgress | None = None, workers: int = 1) -> RunReport:
    """Caller holds the archive writer lease. Atomic output release follows each barrier."""
    if workers < 1:
        raise ValueError("processing workers must be positive")
    prepare_run(database, plugins, retry=retry)
    try:
        dispatch(database, plugins, max_jobs=max_jobs, installation_config=installation_config,
                 pipelines=pipelines, services=services, cancelled=cancelled, failures=failures, message_id=message_id,
                 continue_after_failure=continue_after_failure, progress=progress, workers=workers)
    finally:
        if services is None:
            # Production owns cleanup after canonical publication/checkpointing;
            # the harness has only source snapshots and must preserve those.
            archive = Path(database.execute("PRAGMA database_list").fetchone()[2]).parent
            collect_objects(database, archive)
    return report(database, tuple(p.manifest.kind for p in plugins))


def prepare_run(database: sqlite3.Connection, plugins: tuple[PluginSpec, ...], *, retry: bool = False) -> None:
    """Recover interrupted invocations once at the start of a writer-owned run."""
    registry_hash = fingerprint(plugins)
    incompatible = database.execute("SELECT 1 FROM jobs WHERE registry_hash<>? AND status IN ('pending','running','failed') LIMIT 1", (registry_hash,)).fetchone()
    if incompatible:
        raise ValueError("registry changed; use the explicit reprocess command")
    with database:
        database.execute("UPDATE invocations SET status='failed',error='interrupted worker' WHERE status='running'")
        database.execute("UPDATE jobs SET status='pending' WHERE status='running'")
        if retry:
            database.execute("UPDATE jobs SET status='pending',detail='' WHERE status='failed'")


def dispatch(database: sqlite3.Connection, plugins: tuple[PluginSpec, ...], *,
        max_jobs: int | None = None, installation_config: Path | None = None,
        pipelines: tuple[Pipeline, ...] = ("ingest", "message", "content"),
        services: ProcessorServices | None = None, cancelled: Callable[[], None] | None = None,
        failures: list[Exception] | None = None, message_id: str | None = None,
        continue_after_failure: bool = False, progress: ProcessingProgress | None = None, workers: int = 1) -> None:
    """Workers return results; this coordinator alone advances jobs and commits SQL."""
    if workers < 1:
        raise ValueError("processing workers must be positive")
    registry_hash = fingerprint(plugins)
    failures = failures if failures is not None else []
    active: list[ActiveJob] = []
    processed = 0
    stopped = False
    failure: BaseException | None = None
    with tempfile.TemporaryDirectory(prefix="processing-") as directory:
        try:
            with ThreadPoolExecutor(max_workers=workers) if workers > 1 else nullcontext() as pool:
                def advance(job: ActiveJob, response: InvocationResponse | None = None,
                            error: BaseException | None = None) -> None:
                    nonlocal stopped, failure
                    try:
                        if error is not None:
                            request = job.steps.throw(error)
                        else:
                            request = next(job.steps) if response is None else job.steps.send(response)
                    except StopIteration as done:
                        stopped |= not done.value
                        active.remove(job)
                        return
                    except BaseException as caught:
                        failure = failure or caught
                        stopped = True
                        active.remove(job)
                        return
                    finally:
                        if job.workspace is not None:
                            shutil.rmtree(job.workspace)
                            job.workspace = None
                    if stopped:
                        job.steps.close()
                        active.remove(job)
                        return
                    job.workspace = Path(tempfile.mkdtemp(dir=directory))
                    if pool is not None:
                        job.future = pool.submit(invoke, request, job.workspace, cancelled)
                    else:
                        job.future = Future()
                        try:
                            job.future.set_result(invoke(request, job.workspace, cancelled))
                        except BaseException as caught:
                            job.future.set_exception(caught)

                while True:
                    while not stopped and len(active) < workers and (max_jobs is None or processed < max_jobs):
                        placeholders = ",".join("?" for _ in pipelines)
                        message_filter = "" if message_id is None else "AND json_extract(item_json,'$.message_id')=? "
                        busy_filter = ("AND json_extract(item_json,'$.message_id') NOT IN ("
                                       + ",".join("?" for _ in active) + ") ") if active else ""
                        row = database.execute("SELECT job_id,item_json FROM jobs WHERE status='pending' "
                            f"AND json_extract(item_json,'$.pipeline') IN ({placeholders}) {message_filter}{busy_filter}"
                            "AND (parent_job_id IS NULL OR EXISTS (SELECT 1 FROM jobs parent "
                            "WHERE parent.job_id=jobs.parent_job_id AND parent.status='completed')) ORDER BY job_id LIMIT 1",
                            (*pipelines, *((message_id,) if message_id is not None else ()), *(job.message_id for job in active))).fetchone()
                        if row is None:
                            break
                        worker = next(number for number in range(1, workers + 1) if all(job.worker != number for job in active))
                        job = ActiveJob(message_id=ProcessingObject.model_validate_json(row[1]).message_id, worker=worker,
                            steps=_job(database, plugins, *row, installation_config=installation_config, services=services,
                                failures=failures, continue_after_failure=continue_after_failure, progress=progress,
                                worker=worker, registry_hash=registry_hash))
                        active.append(job)
                        processed += 1
                        advance(job)
                    if not active:
                        break
                    completed, _pending = wait([job.future for job in active if job.future is not None], return_when=FIRST_COMPLETED)
                    for job in tuple(active):
                        if job.future is None or job.future not in completed:
                            continue
                        try:
                            response = job.future.result()
                        except BaseException as caught:
                            advance(job, error=caught)
                        else:
                            advance(job, response)
        finally:
            for job in active:
                job.steps.close()
        if failure is not None:
            raise failure


def _job(database: sqlite3.Connection, plugins: tuple[PluginSpec, ...], job_id: int, payload: str, *,
         installation_config: Path | None, services: ProcessorServices | None, failures: list[Exception],
         continue_after_failure: bool, progress: ProcessingProgress | None, worker: int,
         registry_hash: str) -> Generator[InvocationRequest, InvocationResponse, bool]:
    item = ProcessingObject.model_validate_json(payload).model_copy(update={"job_id": job_id})
    ending = "interrupted"
    try:
        if progress is not None:
            progress(item, "preparing", False, worker)
        try:
            if services is not None:
                item = services.prepare(item)
            recover_config_transaction(item.archive.path)
            input_error = "input digest mismatch" if any(
                content_reference(ref.path).sha256 != ref.sha256 for ref in (item.message_ref, item.content_ref)) else None
        except (OSError, ValueError) as error:
            input_error = f"input unreadable: {error}"
            failures.append(error)
        if input_error:
            with database:
                database.execute("UPDATE jobs SET status='failed',detail=? WHERE job_id=?", (input_error, job_id))
            ending = "failed"
            return continue_after_failure
        selected = subscribers(plugins, item)
        with database:
            database.execute("UPDATE jobs SET status='running' WHERE job_id=?", (job_id,))
        stop_import = False
        explicit_stop = False
        aborted = False
        for rank in sorted({p.manifest.rank for p in selected}):
            if services is not None:
                item = services.prepare(item)
            results: list[tuple[PluginSpec, ProcessingResult]] = []
            failed = False
            for plugin in (p for p in selected if p.manifest.rank == rank):
                if progress is not None:
                    progress(item, plugin.manifest.kind, False, worker)
                result = yield from _checkpoint(database, job_id, plugin, item, installation_config, failures, services)
                if result is None:
                    failed = True
                else:
                    results.append((plugin, result))
                    if result.outcome == "fail-import":
                        break
            outcomes = [result.outcome for _, result in results]
            with database:
                if failed or "fail-import" in outcomes:
                    database.execute("UPDATE jobs SET status='failed',detail=? WHERE job_id=?", (RANK_FAILURE, job_id))
                    stop_import = True
                    explicit_stop = "fail-import" in outcomes
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
            ending = "failed"
            if continue_after_failure and explicit_stop:
                raise RuntimeError("processor requested fail-import")
            return continue_after_failure
        if not aborted:
            if services is not None:
                services.complete(item)
            with database:
                database.execute("UPDATE jobs SET status='completed' WHERE job_id=?", (job_id,))
        if services is not None:
            services.release(item.message_id)
        else:
            collect_objects(database, item.archive.path, message_id=item.message_id)
        ending = "aborted" if aborted else "completed"
        return True
    finally:
        if progress is not None:
            progress(item, ending, True, worker)
