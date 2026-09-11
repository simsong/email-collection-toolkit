"""Requirements: Finder receives exact exported files, never URL shortcut data."""

import sys
from importlib import import_module
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from mailarchiver.file_drag import FILE_DRAGS, FileDrags, native_drag_items, replace_file_pasteboard
from mailarchiver.gui_app import GuiApi
from tests.test_gui_service import SIMPLE_MESSAGE, make_gui_archive


def test_export_tokens_preserve_eml_bytes_and_expire_on_close(tmp_path: Path) -> None:
    """An explicit drag exports verified bytes and grants access only until close."""
    archive = make_gui_archive(tmp_path)
    api = GuiApi(archive, temporary_directory=tmp_path / "exports")
    result = api.prepare_drag([1])
    path = FILE_DRAGS.resolve(result["token"])
    assert path is not None and path.name == result["filename"]
    assert path.suffix == ".eml" and path.read_bytes() == SIMPLE_MESSAGE
    assert "url" not in result
    api.close()
    assert FILE_DRAGS.resolve(result["token"]) is None


def test_file_drags_reject_paths_and_missing_exports(tmp_path: Path) -> None:
    """Dragged text cannot grant access to an arbitrary file or stale export."""
    exports = FileDrags()
    path = tmp_path / "message.eml"
    path.write_bytes(b"Subject: fixture\n\nbody\n")
    token = exports.register(path)
    assert exports.resolve(str(path)) is None
    assert exports.resolve(path.as_uri()) is None
    assert exports.resolve(token) == path
    path.unlink()
    assert exports.resolve(token) is None


@pytest.mark.skipif(sys.platform != "darwin", reason="requires the macOS pasteboard")
@pytest.mark.parametrize("suffix", [".eml", ".zip"])
def test_native_drag_writers_advertise_files_only(tmp_path: Path, suffix: str) -> None:
    """Both WebKit drag APIs produce a real file writer, preserving unusual names."""
    appkit = import_module("AppKit")
    path = tmp_path / f"résumé #100% attachment{suffix}"
    path.write_bytes(b"Subject: exact\r\n\r\nunchanged\xff\r\n")
    token = FILE_DRAGS.register(path)
    pasteboard = appkit.NSPasteboard.pasteboardWithUniqueName()
    try:
        pasteboard.setString_forType_(token, appkit.NSPasteboardTypeString)
        pasteboard.setString_forType_(path.as_uri(), appkit.NSPasteboardTypeURL)
        assert replace_file_pasteboard(pasteboard)
        assert Path(unquote(urlsplit(pasteboard.stringForType_(appkit.NSPasteboardTypeFileURL) or "").path)).samefile(path)
        assert appkit.NSPasteboardTypeURL not in pasteboard.types()
        assert appkit.NSPasteboardTypeString not in pasteboard.types()
        urls = pasteboard.readObjectsForClasses_options_([appkit.NSURL], None)
        assert len(urls) == 1 and Path(urls[0].path()).read_bytes() == path.read_bytes()

        writer = appkit.NSPasteboardItem.alloc().init()
        writer.setString_forType_(token, appkit.NSPasteboardTypeString)
        item = appkit.NSDraggingItem.alloc().initWithPasteboardWriter_(writer)
        item.setDraggingFrame_contents_(((10, 20), (32, 32)), None)
        converted = native_drag_items([item])
        assert converted[0].draggingFrame() == item.draggingFrame()
        pasteboard.clearContents()
        assert pasteboard.writeObjects_([converted[0].item()])
        assert Path(unquote(urlsplit(pasteboard.stringForType_(appkit.NSPasteboardTypeFileURL) or "").path)).samefile(path)
        assert appkit.NSPasteboardTypeURL not in pasteboard.types()
        assert appkit.NSPasteboardTypeString not in pasteboard.types()

        pasteboard.clearContents()
        pasteboard.setString_forType_(path.as_uri(), appkit.NSPasteboardTypeString)
        assert not replace_file_pasteboard(pasteboard)
        assert pasteboard.stringForType_(appkit.NSPasteboardTypeString) == path.as_uri()
        writer.setString_forType_("ordinary text", appkit.NSPasteboardTypeString)
        assert native_drag_items([item])[0] is item
    finally:
        FILE_DRAGS.discard({token})
        pasteboard.releaseGlobally()
