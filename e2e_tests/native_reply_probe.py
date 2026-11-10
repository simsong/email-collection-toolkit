# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise the pinned pywebview reply transport in a disposable native window.
# Real JavaScript calls real Python methods and checks every returned value.
# Synthetic suspend/resume state isolates transport from mail and disk activity.
# Repeated calls expose lost replies without opening an archive or credential store.
# A bounded parent owns the process; this probe closes only its own window.
# Native thread failures and incomplete replies make the probe unsuccessful.
"""DESKTOP: one-way API replies must resolve real WebKit promises without a second acknowledgement."""
from concurrent.futures import Future
from importlib import import_module
import sys
import threading
from typing import Any

from pydantic import BaseModel
import webview

from mailarchiver.native_javascript import API_REPLY_PREFIX, install_cocoa_reply_boundary


class ReplyState(BaseModel):
    calls: int = 0
    suspended: bool = False


class ProbeResult(BaseModel):
    passed: bool
    calls: int = 0
    detail: str = ""


class ReplyApi:
    def __init__(self) -> None:
        self.state = ReplyState()

    def suspend_import(self) -> bool:
        self.state.calls += 1
        self.state.suspended = True
        return True

    def resume_import(self) -> bool:
        self.state.calls += 1
        self.state.suspended = False
        return True

    def history(self) -> dict[str, object]:
        return self.state.model_dump()


SCRIPT = """(async () => {
  for (let i = 0; i < 50; i++) {
    if (!await pywebview.api.suspend_import()) throw new Error('Suspend reply lost');
    if (!(await pywebview.api.history()).suspended) throw new Error('Suspend state lost');
    if (!await pywebview.api.resume_import()) throw new Error('Resume reply lost');
    if ((await pywebview.api.history()).suspended) throw new Error('Resume state lost');
  }
  const final = await pywebview.api.history();
  return {passed: final.calls === 100, calls: final.calls, detail: ''};
})()"""


def main() -> int:
    if sys.platform != "darwin":
        raise RuntimeError("This probe requires Cocoa; Windows needs its own native acceptance")
    install_cocoa_reply_boundary()
    outcome: Future[ProbeResult] = Future()
    failures: list[str] = []
    threading.excepthook = lambda event: failures.append(str(event.exc_value))
    window = webview.create_window("ECT synthetic reply check", html="<p>Synthetic reply check</p>",
                                   js_api=ReplyApi(), hidden=True)
    assert window is not None

    def run() -> None:
        reply: Future[object] = Future()
        try:
            if not window.events.loaded.wait(10):
                raise TimeoutError("Probe page did not load")
            browser = import_module("webview.platforms.cocoa").BrowserView
            original = browser.evaluate_js

            def reject_reply_ack(view: Any, script: str, parse_json: bool) -> Any:
                # Fault injection isolates the SDK acknowledgement that can go missing;
                # actual API execution and one-way WebKit delivery remain native.
                if API_REPLY_PREFIX in script:
                    raise TimeoutError("Redundant native API-reply acknowledgement")
                return original(view, script, parse_json)

            setattr(browser, "evaluate_js", reject_reply_ack)
            window.evaluate_js(SCRIPT, callback=reply.set_result)
            result = ProbeResult.model_validate(reply.result(timeout=30))
            if failures:
                result = ProbeResult(passed=False, detail="; ".join(failures))
            outcome.set_result(result)
        except Exception as error:  # pylint: disable=broad-exception-caught
            outcome.set_result(ProbeResult(passed=False, detail=str(error)))
        finally:
            window.destroy()

    webview.start(run, private_mode=True)
    result = outcome.result(timeout=1)
    print(result.model_dump_json())
    return int(not result.passed or bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
