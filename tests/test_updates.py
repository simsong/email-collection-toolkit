# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Issue #91: persistent tracks and installation exclude all application writers."""

from pathlib import Path

import pytest

from mailarchiver.application import ApplicationController, ApplicationPreferencesStore, IngestJob
from mailarchiver.gui_app import PyWebViewApplication
from mailarchiver.release_versions import release_metadata
from mailarchiver.updates import UpdateService, UpdateStatus, allowed_channels, default_channel
from mailarchiver.writer_lock import ArchiveBusyError, WriterLease


@pytest.mark.parametrize("installed,expected", [("1.0.0a10", "preview"), ("1.0.0b1", "preview"), ("1.0.0", "release")])
def test_migration_retains_explicit_choice_across_upgrade(tmp_path: Path, installed: str, expected: str) -> None:
    """Version-1 preferences gain track defaults without losing Unicode recent paths."""
    store = ApplicationPreferencesStore(tmp_path / "preferences.json")
    store.path.write_text('{"version":1,"last_archive":"/fixture/日本語","recent_archives":["/fixture/日本語"]}')
    controller = ApplicationController(store)
    assert controller.initialize_updates(installed) == expected
    migrated = store.read()
    assert migrated.version == 2
    assert migrated.last_archive == Path("/fixture/日本語")
    assert migrated.recent_archives == [Path("/fixture/日本語")]
    assert default_channel(installed) == expected
    controller.configure_updates("release", False)
    upgraded = ApplicationController(store)
    assert upgraded.initialize_updates("1.1.0b1") == "release"
    assert not upgraded.preferences.automatic_update_checks
    archive = tmp_path / "new.mailarchive"
    upgraded.create_document(archive)
    reopened = ApplicationController(store)
    assert reopened.preferences.update_channel == "release"
    assert not reopened.preferences.automatic_update_checks
    assert reopened.preferences.last_archive == archive
    # Removing a missing recent archive must also retain the update settings.
    with pytest.raises(ValueError):
        reopened.open_recent_document(Path("/fixture/日本語"))
    assert store.read().update_channel == "release"
    assert not store.read().automatic_update_checks


def test_channel_filter_and_build_order_include_stable_after_preview() -> None:
    """Sparkle receives only the additive preview channel; versions never downgrade."""
    assert allowed_channels("release") == frozenset()
    assert allowed_channels("preview") == frozenset({"preview"})
    versions = ["1.0.0a1", "1.0.0a10", "1.0.0a399", "1.0.0b1", "1.0.0b399", "1.0.0", "1.0.1a1"]
    builds = [release_metadata(version)[2] for version in versions]
    assert builds == sorted(set(builds))


def test_definition_replacement_defers_install_and_is_excluded_after_reservation(tmp_path: Path) -> None:
    """Issue #91: definition updates are preserved even though they do not hold archive leases."""
    host = PyWebViewApplication(ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json")))
    host.updates.defer_install(lambda: None)
    try:
        with host.definitions_activity():
            assert not host.updates.resume_install()
        assert host.updates.resume_install()
        with pytest.raises(ValueError, match="installing an update"):
            with host.definitions_activity():
                raise AssertionError("Definition work started after installation reservation")
    finally:
        host.cancel_update_install()


def test_failed_writer_acquisition_does_not_leave_update_permanently_busy(tmp_path: Path) -> None:
    """A real OS-lock contention failure must unwind the process acquisition count."""
    controller = ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json"))
    document = controller.create_document(tmp_path / "archive.mailarchive")
    assert document.path is not None
    lease = WriterLease.acquire(document.path, document.descriptor.identity, "first", "first", "test")
    try:
        with pytest.raises(ArchiveBusyError):
            WriterLease.acquire(document.path, document.descriptor.identity, "second", "second", "test")
        assert not WriterLease.reserve_update()
        lease.release()
        assert WriterLease.reserve_update()
    finally:
        lease.release()
        WriterLease.cancel_update()


def test_pending_install_waits_for_real_writer_and_reserves_against_new_writes(tmp_path: Path) -> None:
    """No new lease or archive can be created between idle reservation and relaunch."""
    controller = ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json"))
    document = controller.create_document(tmp_path / "archive.mailarchive")
    assert document.path is not None
    host = PyWebViewApplication(controller)
    completed: list[str] = []
    host.updates.defer_install(lambda: completed.append("installed"))
    lease = WriterLease.acquire(document.path, document.descriptor.identity, "fixture", "fixture", "test")
    try:
        assert not host.updates.resume_install()
        assert not completed
        lease.release()
        assert host.updates.resume_install()
        assert not host.updates.resume_install()
        assert completed == ["installed"]
        assert host.updates.status.phase == "installing"
        with pytest.raises(ArchiveBusyError, match="update installation"):
            controller.create_document(tmp_path / "must-not-exist.mailarchive")
        assert not (tmp_path / "must-not-exist.mailarchive").exists()
        host.updates.fail("Synthetic canceled installation")
        with WriterLease.acquire(document.path, document.descriptor.identity, "retry", "retry", "test"):
            assert host.updates.status.phase == "error"
    finally:
        lease.release()
        host.cancel_update_install()


def test_install_continuation_failure_restores_real_writer_access(tmp_path: Path) -> None:
    """Issue #91: a native continuation error clears reservation and runs only once."""
    controller = ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json"))
    document = controller.create_document(tmp_path / "archive.mailarchive")
    assert document.path is not None
    host = PyWebViewApplication(controller)
    attempts: list[bool] = []

    def failed_install() -> None:
        attempts.append(True)
        raise RuntimeError("synthetic native installation error")

    host.updates.defer_install(failed_install)
    try:
        with pytest.raises(RuntimeError, match="native installation"):
            host.updates.resume_install()
        assert host.updates.status.phase == "error"
        assert "native installation" in host.updates.status.detail
        assert not host.updates.resume_install()
        assert attempts == [True]
        with WriterLease.acquire(document.path, document.descriptor.identity, "recovered", "fixture", "test"):
            assert not host._updating
    finally:
        host.cancel_update_install()


def test_pending_install_waits_for_job_completion_even_after_lease_release(tmp_path: Path) -> None:
    """An active content job cannot be terminated during its checkpoint/worker tail."""
    controller = ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json"))
    document = controller.create_document(tmp_path / "archive.mailarchive")
    window = controller.new_search_window(document)
    assert document.path is not None
    host = PyWebViewApplication(controller)
    job = IngestJob(operation_id="fixture", owner_window_id=window.window_id, kind="content")
    lease = WriterLease.acquire(document.path, document.descriptor.identity, "fixture", "fixture", "test")
    controller.begin_ingest(document.descriptor.document_id, job, lease)
    service = UpdateService(UpdateStatus(version="1.0.0", channel="release"), host.reserve_update_install, host.cancel_update_install)
    installed: list[bool] = []
    service.defer_install(lambda: installed.append(True))
    try:
        lease.release()
        assert not service.resume_install()
        assert not job.stop.is_set()
        controller.finish_ingest(document.descriptor.document_id, job.operation_id, published=False)
        assert service.resume_install()
        assert installed == [True]
    finally:
        lease.release()
        host.cancel_update_install()
