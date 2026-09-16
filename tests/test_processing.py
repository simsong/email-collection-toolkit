# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""PLUGINS.md API v2: actual subprocess dispatch, persistence and abort requirements."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from mailarchiver.processing.api import RAW_MESSAGE
from mailarchiver.processing.registry import fingerprint, load_processors
from mailarchiver.processing.runtime import run
from mailarchiver.processing.store import connect, report, submit

FIXTURES = Path(__file__).parent / "processing_plugins"
MESSAGE = Path(__file__).parent / "data/credible_date_with_quoted_body.eml"


def plugin(root: Path, kind: str, code: str, *, rank: int = 1, emits: str = "",
           subscribes: str = RAW_MESSAGE, timeout: float = 60, scope: str = "both") -> None:
    directory = root / "processors" / kind
    directory.mkdir(parents=True)
    directory.joinpath("plugin.toml").write_text(
        f'api_version=2\nplugin_type="processor"\nkind="{kind}"\nname="{kind}"\n'
        f'implementation_version="1"\nentrypoint="plugin:create_plugin"\npipeline="ingest"\n'
        f'subscribes=["{subscribes}"]\nemits=[{emits}]\nrank={rank}\n'
        f'timeout_seconds={timeout}\nscope="{scope}"\n')
    directory.joinpath("plugin.py").write_text(
        "from mailarchiver.processing.api import ProcessingResult, Emission\n"
        "import time\n"
        "class Plugin:\n"
        "    def process(self, item):\n"
        + "".join(f"        {line}\n" for line in code.splitlines())
        + "\ndef create_plugin():\n    return Plugin()\n")


def test_cli_three_pipelines_and_resume(tmp_path: Path) -> None:
    """Real CLI across three invocations; source copies and checkpointed plugins persist."""
    archive = tmp_path / "archive"
    base = [sys.executable, "-m", "mailarchiver.processing", "--archive", str(archive),
            "--plugin-dir", str(FIXTURES)]
    def cli(*args: str) -> str:
        return subprocess.run([*base, *args], capture_output=True, text=True, check=True).stdout
    cli("init")
    cli("submit", str(MESSAGE))
    first = json.loads(cli("run", "--max-jobs", "1"))
    assert first["completed"] == 1 and first["pending"] == 1
    last = json.loads(cli("run"))
    assert last["completed"] == 5 and last["pending"] == 0
    assert all(s["invocations"] == 1 for s in last["statistics"])
    cli("submit", str(MESSAGE))
    assert json.loads(cli("run"))["statistics"] == last["statistics"]
    with connect(archive) as database:
        stored = Path(database.execute("SELECT content_path FROM messages").fetchone()[0])
        assert stored.read_bytes() == MESSAGE.read_bytes()
        assert database.execute("SELECT count(*) FROM occurrences").fetchone()[0] == 1


def test_rank_barrier_and_part_abort(tmp_path: Path) -> None:
    """Equal-rank work finishes, abort blocks later ranks and emitted descendants."""
    plugin(tmp_path, "a-emitter", 'return ProcessingResult(emissions=(Emission(content_ref=item.content_ref, content_type="text/plain"),))',
           emits='"text/plain"')
    plugin(tmp_path, "b-abort", 'return ProcessingResult(outcome="abort-part")')
    plugin(tmp_path, "c-later", 'raise AssertionError("must not run")', rank=2)
    plugin(tmp_path, "d-child", 'raise AssertionError("must not run")', subscribes="text/plain")
    plugins = load_processors((tmp_path,))
    with connect(tmp_path / "archive", create=True) as database:
        submit(database, tmp_path / "archive", MESSAGE, fingerprint(plugins))
        result = run(database, plugins)
        assert result.aborted == 1 and result.pending == 0
        assert [(s.kind, s.invocations) for s in result.statistics] == [
            ("a-emitter", 1), ("b-abort", 1), ("d-child", 0), ("c-later", 0)]


@pytest.mark.parametrize("outcome", ["abort-message", "fail-import"])
def test_message_and_import_aborts(tmp_path: Path, outcome: str) -> None:
    plugin(tmp_path, "abort", f'return ProcessingResult(outcome="{outcome}")')
    plugins = load_processors((tmp_path,))
    with connect(tmp_path / "archive", create=True) as database:
        submit(database, tmp_path / "archive", MESSAGE, fingerprint(plugins))
        other = tmp_path / "other.eml"
        other.write_bytes(b"Subject: second\n\nsecond")
        submit(database, tmp_path / "archive", other, fingerprint(plugins))
        result = run(database, plugins)
        if outcome == "fail-import":
            assert result.failed == 1 and result.pending == 1
        else:
            assert result.aborted == 2 and result.pending == 0


def test_timeout_kills_worker_and_retry_reuses_checkpoint(tmp_path: Path) -> None:
    """Noncooperative plugin cannot emit after timeout; previous successful rank is reused."""
    plugin(tmp_path, "first", 'return ProcessingResult()')
    plugin(tmp_path, "slow", 'time.sleep(10)\nreturn ProcessingResult()', rank=2, timeout=0.2)
    plugins = load_processors((tmp_path,))
    with connect(tmp_path / "archive", create=True) as database:
        submit(database, tmp_path / "archive", MESSAGE, fingerprint(plugins))
        result = run(database, plugins)
        assert result.failed == 1
        assert "timeout" in database.execute("SELECT error FROM invocations WHERE kind='slow'").fetchone()[0]
        again = run(database, plugins, retry=True)
        assert again.statistics[0].invocations == 1
        assert again.statistics[1].invocations == 2
        assert again.statistics[1].errors == 2
        assert again.statistics[1].shortest is not None
        assert again.statistics[1].average == pytest.approx(again.statistics[1].total / 2)


def test_discovery_rejects_cycles_before_import(tmp_path: Path) -> None:
    plugin(tmp_path, "cycle", 'raise AssertionError("not imported")', emits=f'"{RAW_MESSAGE}"')
    with pytest.raises(ValueError, match="cyclic"):
        load_processors((tmp_path,))


def test_undeclared_output_fails_without_publishing(tmp_path: Path) -> None:
    plugin(tmp_path, "invalid", 'return ProcessingResult(emissions=(Emission(content_ref=item.content_ref, content_type="text/plain"),))')
    plugins = load_processors((tmp_path,))
    with connect(tmp_path / "archive", create=True) as database:
        submit(database, tmp_path / "archive", MESSAGE, fingerprint(plugins))
        assert run(database, plugins).failed == 1
        assert database.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1


def test_identity_schema_allows_simultaneous_affiliations_and_preserves_manual_data(tmp_path: Path) -> None:
    """Fresh schema supports picker identity and dated affiliations, independent of jobs."""
    with connect(tmp_path / "archive", create=True) as database:
        database.execute("INSERT INTO persons VALUES(1,'Canonical Person',1)")
        database.execute("INSERT INTO addresses VALUES(1,'alias@example.org','alias','example.org')")
        database.execute("INSERT INTO person_addresses VALUES(1,1,1)")
        database.execute("INSERT INTO organizations VALUES(1,'First',1)")
        database.execute("INSERT INTO organizations VALUES(2,'Second',0)")
        database.execute("INSERT INTO affiliations VALUES(1,1,1,'2000-01-01','2010-12-31',1)")
        database.execute("INSERT INTO affiliations VALUES(2,1,2,'2005-01-01',NULL,0)")
        database.commit()
        with pytest.raises(sqlite3.IntegrityError):
            database.execute("INSERT INTO affiliations VALUES(3,1,1,'2020-01-01','2010-01-01',0)")
        assert database.execute("SELECT background_color,text_color FROM tags").fetchone() == ("#f2f2f2", None)
        assert report(database, ()).completed == 0
        assert database.execute("SELECT canonical_name,manual FROM persons").fetchone() == ("Canonical Person", 1)
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []


def test_part_abort_keeps_sibling_work(tmp_path: Path) -> None:
    plugin(tmp_path, "split", 'return ProcessingResult(emissions=(Emission(content_ref=item.content_ref, content_type="text/plain", part_path=(1,)), Emission(content_ref=item.content_ref, content_type="text/plain", part_path=(2,), scope="attachment")))',
           emits='"text/plain"')
    plugin(tmp_path, "body-abort", 'return ProcessingResult(outcome="abort-part")', subscribes="text/plain", scope="body")
    plugin(tmp_path, "attachment", 'return ProcessingResult()', subscribes="text/plain", scope="attachment")
    plugins = load_processors((tmp_path,))
    with connect(tmp_path / "archive", create=True) as database:
        submit(database, tmp_path / "archive", MESSAGE, fingerprint(plugins))
        result = run(database, plugins)
        assert result.completed == 2 and result.aborted == 1
        assert next(s for s in result.statistics if s.kind == "attachment").invocations == 1


def test_explicit_reprocess_retains_manual_edits(tmp_path: Path) -> None:
    from mailarchiver.processing.store import reprocess
    plugin(tmp_path, "first", 'return ProcessingResult()')
    plugins = load_processors((tmp_path,))
    archive = tmp_path / "archive"
    with connect(archive, create=True) as database:
        submit(database, archive, MESSAGE, fingerprint(plugins))
        run(database, plugins)
        database.execute("INSERT INTO persons VALUES(1,'Manual Name',1)")
        database.commit()
        manifest = tmp_path / "processors/first/plugin.toml"
        manifest.write_text(manifest.read_text().replace('implementation_version="1"', 'implementation_version="2"'))
        changed = load_processors((tmp_path,))
        reprocess(database, archive, fingerprint(changed))
        result = run(database, changed)
        assert result.completed == 2
        assert database.execute("SELECT canonical_name FROM persons").fetchone()[0] == "Manual Name"
        assert database.execute("SELECT version FROM invocations ORDER BY invocation_id").fetchall() == [("1",), ("2",)]


def test_interrupted_rank_replays_without_reinvoking_completed_plugin(tmp_path: Path) -> None:
    """Persist one success and an abandoned invocation as an interrupted worker would."""
    plugin(tmp_path, "first", 'return ProcessingResult()')
    plugin(tmp_path, "second", 'return ProcessingResult()', rank=2)
    plugins = load_processors((tmp_path,))
    archive = tmp_path / "archive"
    with connect(archive, create=True) as database:
        submit(database, archive, MESSAGE, fingerprint(plugins))
        database.execute("UPDATE jobs SET status='running'")
        database.execute("INSERT INTO invocations(job_id,kind,version,rank,status,result_json) VALUES(1,'first','1',1,'completed','{}')")
        database.execute("INSERT INTO invocations(job_id,kind,version,rank,status) VALUES(1,'second','1',2,'running')")
        database.commit()
    with connect(archive) as reopened:
        result = run(reopened, plugins)
        assert result.completed == 1
        assert result.statistics[0].invocations == 1
        assert result.statistics[1].invocations == 2
        assert result.statistics[1].errors == 1


def test_dependency_and_entrypoint_validation_before_execution(tmp_path: Path) -> None:
    plugin(tmp_path, "invalid", 'return ProcessingResult()')
    manifest = tmp_path / "processors/invalid/plugin.toml"
    manifest.write_text(manifest.read_text() + 'requires=["missing"]\n')
    with pytest.raises(ValueError, match="dependency"):
        load_processors((tmp_path,))
    manifest.write_text(manifest.read_text().replace('requires=["missing"]', '').replace('plugin:create_plugin', '../outside:create_plugin'))
    with pytest.raises(ValueError, match="entrypoint"):
        load_processors((tmp_path,))


def test_modified_input_is_not_processed(tmp_path: Path) -> None:
    plugins = load_processors((FIXTURES,))
    archive = tmp_path / "archive"
    with connect(archive, create=True) as database:
        submit(database, archive, MESSAGE, fingerprint(plugins))
        path = database.execute("SELECT content_path FROM messages").fetchone()[0]
        Path(path).write_bytes(b"altered")
        result = run(database, plugins)
        assert result.failed == 1
        assert all(stat.invocations == 0 for stat in result.statistics)


def test_timeout_cannot_write_late_result(tmp_path: Path) -> None:
    import time
    plugin(tmp_path, "late", 'time.sleep(1)\n(item.archive.path / "late-write").write_text("bad")\nreturn ProcessingResult()', timeout=0.3)
    plugins = load_processors((tmp_path,))
    archive = tmp_path / "archive"
    with connect(archive, create=True) as database:
        submit(database, archive, MESSAGE, fingerprint(plugins))
        assert run(database, plugins).failed == 1
        time.sleep(1.1)
        assert not (archive / "late-write").exists()


def test_failed_parent_blocks_previously_emitted_work(tmp_path: Path) -> None:
    """A queued emission cannot run past a later failed rank, even on another run."""
    plugin(tmp_path, "emit", 'return ProcessingResult(emissions=(Emission(content_ref=item.content_ref, content_type="text/plain"),))',
           emits='"text/plain"')
    plugin(tmp_path, "fail", 'return ProcessingResult(outcome="fail-import")', rank=2)
    plugin(tmp_path, "child", 'raise AssertionError("must stay blocked")', subscribes="text/plain")
    plugins = load_processors((tmp_path,))
    archive = tmp_path / "archive"
    with connect(archive, create=True) as database:
        submit(database, archive, MESSAGE, fingerprint(plugins))
        first = run(database, plugins)
        assert first.failed == 1 and first.pending == 1
        again = run(database, plugins)
        assert again == first
        assert next(s for s in again.statistics if s.kind == "child").invocations == 0
