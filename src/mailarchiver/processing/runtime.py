# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Serial rank-barrier dispatcher with durable checkpoints and killable workers."""
from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .api import (
    InvocationRequest, InvocationResponse, PluginSpec, ProcessingObject,
    ProcessingResult, RAW_MESSAGE, RunReport,
)
from .registry import fingerprint, subscribers
from .store import content_reference, enqueue, report, snapshot


def invoke(plugin: PluginSpec, item: ProcessingObject) -> InvocationResponse:
    with tempfile.TemporaryDirectory(prefix="processor-") as directory:
        request_path, result_path = Path(directory) / "request.json", Path(directory) / "result.json"
        request_path.write_text(InvocationRequest(plugin=plugin, item=item).model_dump_json())
        with (Path(directory) / "log").open("wb") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "mailarchiver.processing.worker", str(request_path), str(result_path)],
                stdout=log, stderr=log, start_new_session=os.name != "nt",
            )
            try:
                process.wait(timeout=plugin.manifest.timeout_seconds)
            except BaseException as error:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                if isinstance(error, subprocess.TimeoutExpired):
                    return InvocationResponse(error="processor timeout; worker terminated")
                raise
        if process.returncode != 0 or not result_path.exists():
            return InvocationResponse(error=f"worker exited without a result (code {process.returncode})")
        try:
            return InvocationResponse.model_validate_json(result_path.read_text())
        except ValueError as error:
            return InvocationResponse(error=f"invalid worker result: {error}")


def validate_result(plugin: PluginSpec, item: ProcessingObject, result: ProcessingResult) -> None:
    for emitted in result.emissions:
        if emitted.content_type not in plugin.manifest.emits:
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


def _checkpoint(database: sqlite3.Connection, job_id: int, plugin: PluginSpec, item: ProcessingObject) -> ProcessingResult | None:
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
        response = invoke(plugin, item)
        if response.result is not None:
            validate_result(plugin, item, response.result)
    except KeyboardInterrupt:
        with database:
            database.execute("UPDATE invocations SET status='failed',elapsed=?,error='cancelled' WHERE invocation_id=?",
                             (time.monotonic() - started, invocation_id))
        raise
    except Exception as error:
        response = InvocationResponse(error=f"{type(error).__name__}: {error}")
    elapsed = time.monotonic() - started
    success = response.result is not None and response.error is None
    with database:
        database.execute("UPDATE invocations SET status=?,elapsed=?,result_json=?,error=? WHERE invocation_id=?",
                         ("completed" if success else "failed", elapsed,
                          response.result.model_dump_json() if success and response.result else None,
                          response.error, invocation_id))
    return response.result if success else None


def _publish(database: sqlite3.Connection, item: ProcessingObject, plugin: PluginSpec,
             result: ProcessingResult, registry_hash: str, job_id: int) -> None:
    for emitted in result.emissions:
        derived = ProcessingObject(
            application=item.application, archive=item.archive, message_id=item.message_id,
            message_ref=item.message_ref, content_ref=snapshot(item.archive.path, emitted.content_ref.path), content_type=emitted.content_type,
            pipeline=item.pipeline, part_path=emitted.part_path, scope=emitted.scope,
            synthetic=emitted.synthetic, parent_message_id=item.parent_message_id,
            scan_provenance=item.scan_provenance, producer=f"{plugin.manifest.kind}:{plugin.manifest.implementation_version}")
        enqueue(database, derived, registry_hash, job_id)
    for handoff in result.handoffs:
        following = ProcessingObject(
            application=item.application, archive=item.archive, message_id=item.message_id,
            message_ref=item.message_ref, content_ref=item.message_ref, content_type=RAW_MESSAGE,
            pipeline=handoff.pipeline, parent_message_id=item.parent_message_id,
            scan_provenance=item.scan_provenance)
        enqueue(database, following, registry_hash, job_id)


def _abort(database: sqlite3.Connection, item: ProcessingObject, whole_message: bool) -> None:
    for job_id, payload in database.execute("SELECT job_id,item_json FROM jobs WHERE status IN ('pending','running')").fetchall():
        candidate = ProcessingObject.model_validate_json(payload)
        if candidate.message_id == item.message_id and (whole_message or candidate.part_path[:len(item.part_path)] == item.part_path):
            database.execute("UPDATE jobs SET status='aborted' WHERE job_id=?", (job_id,))


def run(database: sqlite3.Connection, plugins: tuple[PluginSpec, ...], *, retry: bool = False,
        max_jobs: int | None = None) -> RunReport:
    """Caller holds the archive writer lease. Atomic output release follows each barrier."""
    registry_hash = fingerprint(plugins)
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
        row = database.execute("SELECT job_id,item_json FROM jobs WHERE status='pending' AND (parent_job_id IS NULL OR parent_job_id IN (SELECT job_id FROM jobs WHERE status='completed')) ORDER BY job_id LIMIT 1").fetchone()
        if row is None:
            break
        job_id, payload = row
        item = ProcessingObject.model_validate_json(payload)
        if any(content_reference(ref.path).sha256 != ref.sha256 for ref in (item.message_ref, item.content_ref)):
            with database:
                database.execute("UPDATE jobs SET status='failed',detail='input digest mismatch' WHERE job_id=?", (job_id,))
            break
        selected = subscribers(plugins, item)
        with database:
            database.execute("UPDATE jobs SET status='running' WHERE job_id=?", (job_id,))
        stop_import = False
        aborted = False
        for rank in sorted({p.manifest.rank for p in selected}):
            results: list[tuple[PluginSpec, ProcessingResult]] = []
            failed = False
            for plugin in (p for p in selected if p.manifest.rank == rank):
                result = _checkpoint(database, job_id, plugin, item)
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
                    _abort(database, item, "abort-message" in outcomes)
                    aborted = True
                else:
                    for plugin, result in results:
                        _publish(database, item, plugin, result, registry_hash, job_id)
            if stop_import or aborted:
                break
        if stop_import:
            break
        if not aborted:
            with database:
                database.execute("UPDATE jobs SET status='completed' WHERE job_id=?", (job_id,))
        processed += 1
    return report(database, tuple(p.manifest.kind for p in plugins))
