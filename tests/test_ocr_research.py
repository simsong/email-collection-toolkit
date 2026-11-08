# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Verify ownership when a research OCR application can continue after returning.
# Only its unavailable licensed command boundary is substituted in this unit test.
# The real helper copies input, retains failures and cleans completed private work.
# A hostile external-command stand-in deletes its input copy to test source safety.
# No native OCR application is launched and no personal PDF or archive is touched.
"""ACQUISITION: external research tools never own source files or lost workspaces."""
from pathlib import Path
import shutil

import pytest

from scripts import ocr_experiment


@pytest.mark.parametrize("fails", [False, True])
def test_abbyy_owns_only_a_copy_and_preserves_incomplete_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fails: bool,
) -> None:
    source, destination = tmp_path / "source.pdf", tmp_path / "text.txt"
    source.write_bytes(b"synthetic private PDF input")
    workspaces: list[Path] = []
    failure = RuntimeError("external export is still active")

    def external(command: list[str], timeout: int = 1800) -> bytes:
        if command[0] == "osascript":
            assert timeout == 3600
            copied, recognized = Path(command[2]), Path(command[3])
            assert copied != source and copied.read_bytes() == source.read_bytes()
            workspaces.append(copied.parent)
            copied.unlink()  # An external application's deletion preference is harmless.
            recognized.write_bytes(b"synthetic generated PDF")
            if fails:
                raise failure
        else:
            assert command[0] == "pdftotext"
            assert Path(command[2]).read_bytes() == b"synthetic generated PDF"
            Path(command[3]).write_text("recognized text", encoding="utf-8")
        return b""

    monkeypatch.setattr(ocr_experiment, "command_output", external)
    try:
        if fails:
            with pytest.raises(RuntimeError, match="still active") as raised:
                ocr_experiment.abbyy_text(source, destination)
            assert raised.value is failure and workspaces[0].is_dir()
            assert str(workspaces[0]) in " ".join(getattr(failure, "__notes__", []))
            assert not destination.exists()
        else:
            ocr_experiment.abbyy_text(source, destination)
            assert destination.read_text() == "recognized text" and not workspaces[0].exists()
        assert source.read_bytes() == b"synthetic private PDF input"
    finally:
        for workspace in workspaces:
            if workspace.exists():
                shutil.rmtree(workspace)  # This stand-in has no surviving external work.
