# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Bound native JavaScript replies at the application's pinned pywebview adapters.
# Upstream synchronous semaphores can survive destruction of their webviews.
# Each window owns pending reply futures and releases them on actual Close.
# Cocoa and WebView2 evaluate on their UI threads; callers wait off those threads.
# Native completion, errors and window closure settle each future exactly once.
# A final failure deadline contains missing SDK callbacks without polling.
"""Owned native reply lifetimes for the desktop bridge."""
from __future__ import annotations

from concurrent.futures import Future
from importlib import import_module
import json
import os
import sys
from threading import RLock
from typing import Any

REPLY_TIMEOUT_SECONDS = 15
_registry_lock = RLock()


class WindowReplies:
    """Keep SDK callbacks and Close from settling the same operation concurrently."""

    def __init__(self) -> None:
        self.lock = RLock()
        self.closed = False
        self.pending: set[Future[Any]] = set()

    def add(self) -> Future[Any]:
        future: Future[Any] = Future()
        with self.lock:
            if self.closed:
                future.set_exception(RuntimeError("Native JavaScript window is closed"))
            else:
                self.pending.add(future)
        return future

    def finish(self, future: Future[Any], value: Any = None, error: Exception | None = None) -> None:
        with self.lock:
            if future.done():
                return
            if error is None:
                future.set_result(value)
            else:
                future.set_exception(error)

    def remove(self, future: Future[Any]) -> None:
        with self.lock:
            self.pending.discard(future)
            future.cancel()

    def close(self) -> None:
        with self.lock:
            self.closed = True
            for future in tuple(self.pending):
                self.finish(future, error=RuntimeError("Native JavaScript window closed before its reply"))


_windows: dict[str, WindowReplies] = {}


def _replies_for(window: Any) -> WindowReplies:
    with _registry_lock:
        replies = _windows.get(window.uid)
        if replies is None:
            replies = WindowReplies()
            _windows[window.uid] = replies

            def closed() -> None:
                replies.close()
                with _registry_lock:
                    if _windows.get(window.uid) is replies:
                        _windows.pop(window.uid)

            window.events.closed += closed
        return replies


def install_cocoa_reply_boundary() -> None:
    """Adapt only this application's Cocoa backend, preserving pywebview's public serialization."""
    if sys.platform != "darwin":
        return
    browser = import_module("webview.platforms.cocoa").BrowserView
    if getattr(browser, "_mailarchiver_reply_boundary", False):
        return
    foundation = import_module("Foundation")
    webkit = import_module("WebKit")
    helper = import_module("PyObjCTools.AppHelper")

    def evaluate(view: Any, script: str, parse_json: bool) -> Any:
        if foundation.NSThread.isMainThread():
            raise RuntimeError("Synchronous native JavaScript must run off Cocoa's main thread")
        window = view.pywebview_window
        replies = _replies_for(window)
        result = replies.add()

        def received(value: Any, error: Any) -> None:
            if error is not None:
                # Promise bridge callbacks deliver their value separately. WebKit
                # may report that their immediate return cannot be serialized.
                if (error.domain() == webkit.WKErrorDomain and
                        error.code() == webkit.WKErrorJavaScriptResultTypeIsUnsupported):
                    replies.finish(result)
                    return
                replies.finish(result, error=RuntimeError(str(error.localizedDescription())))
                return
            if parse_json and value:
                try:
                    value = json.loads(value)
                except (TypeError, ValueError):
                    pass  # Match the SDK's raw-value fallback, without losing native errors.
            replies.finish(result, value)

        def dispatch() -> None:
            if result.done():
                return
            if window.events.closed.is_set() or view.webview is None:
                replies.finish(result, error=RuntimeError("Native JavaScript window is closed"))
                return
            try:
                view.webview.evaluateJavaScript_completionHandler_(script, received)
            except Exception as error:  # pylint: disable=broad-exception-caught
                replies.finish(result, error=error)

        try:
            helper.callAfter(dispatch)
            return result.result(timeout=REPLY_TIMEOUT_SECONDS)
        finally:
            replies.remove(result)

    setattr(browser, "evaluate_js", evaluate)
    setattr(browser, "_mailarchiver_reply_boundary", True)


def install_windows_reply_boundary() -> None:
    """Adapt the initialized WebView2 renderer without loading CLR on other hosts."""
    if os.name != "nt":
        return
    backend = import_module("webview.platforms.edgechromium")
    browser = backend.EdgeChrome
    if getattr(browser, "_mailarchiver_reply_boundary", False):
        return

    def evaluate(view: Any, script: str, parse_json: bool) -> Any:
        control = view.webview
        if not control.InvokeRequired:
            raise RuntimeError("Synchronous native JavaScript requires a ready WebView2 and a caller off its UI thread")
        window = view.pywebview_window
        replies = _replies_for(window)
        result = replies.add()

        def received(task: Any) -> None:
            try:
                # Task.Result propagates cancellation and native execution faults.
                value = json.loads(task.Result)
                if parse_json and value is not None:
                    try:
                        value = json.loads(value)
                    except (TypeError, ValueError):
                        pass  # Preserve the SDK's two-layer serialization and raw fallback.
                replies.finish(result, value)
            except Exception as error:  # pylint: disable=broad-exception-caught
                replies.finish(result, error=error)

        def dispatch() -> None:
            if result.done():
                return
            try:
                if window.events.closed.is_set() or control.IsDisposed:
                    raise RuntimeError("Native JavaScript window is closed")
                control.ExecuteScriptAsync(script).ContinueWith(
                    backend.Action[backend.Task[backend.String]](received), view.syncContextTaskScheduler,
                )
            except Exception as error:  # pylint: disable=broad-exception-caught
                replies.finish(result, error=error)

        try:
            control.BeginInvoke(backend.Action(dispatch))
            return result.result(timeout=REPLY_TIMEOUT_SECONDS)
        finally:
            replies.remove(result)

    setattr(browser, "evaluate_js", evaluate)
    setattr(browser, "_mailarchiver_reply_boundary", True)
