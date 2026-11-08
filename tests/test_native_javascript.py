# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Validate ownership of pending native JavaScript replies without replacing a webview.
# Real futures and concurrent callbacks exercise the shared reply-lifetime logic.
# Close and native completion may race, but must settle every reply exactly once.
# Removal cancels abandoned requests so a late SDK callback cannot publish them.
# Platform integration is separately exercised by the actual native reader probe.
"""DESKTOP: native reply cleanup must never leave a closed-window waiter blocked."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from mailarchiver.native_javascript import WindowReplies


def test_close_releases_pending_and_rejects_new_replies() -> None:
    replies = WindowReplies()
    pending = replies.add()
    replies.close()
    with pytest.raises(RuntimeError, match="closed before its reply"):
        pending.result(timeout=1)
    with pytest.raises(RuntimeError, match="window is closed"):
        replies.add().result(timeout=1)
    replies.remove(pending)
    assert not replies.pending


def test_native_completion_and_close_can_race_without_double_publication() -> None:
    replies = WindowReplies()
    pending, other = replies.add(), replies.add()
    ready = Barrier(2)

    def finish() -> None:
        ready.wait(timeout=5)
        replies.finish(pending, "native result")

    with ThreadPoolExecutor(max_workers=1) as pool:
        completed = pool.submit(finish)
        ready.wait(timeout=5)
        replies.close()
        completed.result(timeout=5)
    if pending.exception(timeout=1) is None:
        assert pending.result() == "native result"
    else:
        with pytest.raises(RuntimeError, match="closed before its reply"):
            pending.result()
    with pytest.raises(RuntimeError, match="closed before its reply"):
        other.result(timeout=1)


def test_late_native_callback_cannot_publish_an_abandoned_reply() -> None:
    replies = WindowReplies()
    pending = replies.add()
    replies.remove(pending)
    replies.finish(pending, "obsolete result")
    assert pending.cancelled() and not replies.pending
