# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Freeze and exercise Sparkle's real PyObjC controller and native block ABI."""

from __future__ import annotations

import argparse
import plistlib
import subprocess
import sys
import time
from importlib import import_module
from pathlib import Path
from uuid import uuid4
from typing import Any

from pydantic import BaseModel

from mailarchiver.sparkle import show_preferences, start_installed_updater
from mailarchiver.update_metadata import SPARKLE_FEED_URL, SPARKLE_PUBLIC_KEY, SPARKLE_VERSION
from mailarchiver.updates import UpdateService, UpdateStatus

ROOT = Path(__file__).resolve().parents[1]
PROBE_SOURCE = '''// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
#import <Foundation/Foundation.h>
@protocol ProbeDelegate
- (NSSet *)allowedChannelsForUpdater:(id)updater;
- (BOOL)updater:(id)updater shouldPostponeRelaunchForUpdate:(id)item untilInvokingBlock:(void (^)(void))handler;
@end
static int installs = 0;
int probe_channels(id<ProbeDelegate> delegate) { return (int)[delegate allowedChannelsForUpdater:nil].count; }
int probe_postpone(id<ProbeDelegate> delegate) {
    return [delegate updater:nil shouldPostponeRelaunchForUpdate:nil untilInvokingBlock:^{ installs++; }];
}
int probe_installs(void) { return installs; }
'''


class ProbeReport(BaseModel):
    frozen: bool
    controller: str
    build: str
    preview_channels: int
    release_channels: int
    postponed: bool
    continuation_calls: int
    https_check: bool = False
    standard_ui_windows: list[str] = []
    preferences_ui: bool = False
    automatic_checks: bool = False
    continuation_error_recovered: bool = False


def exercise_preferences(service: UpdateService) -> None:
    """Operate actual Cocoa controls and apply the user choice through the production pane."""
    appkit = import_module("AppKit")
    foundation = import_module("Foundation")
    failures: list[str] = []
    saved: list[tuple[str, bool]] = []

    def descendants(view: Any) -> list[Any]:
        result = [view]
        for child in view.subviews():
            result.extend(descendants(child))
        return result

    def inspect(timer: Any) -> None:
        timer.invalidate()
        application = appkit.NSApplication.sharedApplication()
        try:
            window = application.modalWindow()
            controls = descendants(window.contentView())
            popup = next(control for control in controls if control.respondsToSelector_("indexOfSelectedItem"))
            assert popup.itemTitles() == ["Release updates", "Preview updates — alpha, beta, and release"]
            popup.selectItemAtIndex_(1)
            checkbox = next(control for control in controls if control.respondsToSelector_("title") and
                            control.title() == "Check for updates automatically (daily)")
            checkbox.setState_(0)
            assert window.frame().size.width >= 470
            done = next(control for control in controls if control.respondsToSelector_("title") and control.title() == "Done")
            done.performClick_(None)
        except Exception as error:  # pylint: disable=broad-exception-caught
            failures.append(str(error))
            application.stopModalWithCode_(0)

    timer = foundation.NSTimer.timerWithTimeInterval_repeats_block_(0.3, False, inspect)
    foundation.NSRunLoop.mainRunLoop().addTimer_forMode_(timer, appkit.NSModalPanelRunLoopMode)
    show_preferences(service, lambda channel, automatic: saved.append((channel, automatic)))
    if failures or saved != [("preview", False)] or service.status.channel != "preview":
        raise AssertionError(f"Native preference choices failed: {failures}; {saved}")


def exercise(feed_check: bool) -> None:
    """Invoke the delegate from compiled Objective-C, beyond Python-only dispatch."""
    foundation = import_module("Foundation")
    appkit = import_module("AppKit")
    objc = import_module("objc")
    ctypes = import_module("ctypes")
    appkit.NSApplication.sharedApplication()
    bundle = foundation.NSBundle.mainBundle()
    service = UpdateService(UpdateStatus(version="1.0.0a1", channel="preview", automatic_checks=False), lambda: True)
    backend = start_installed_updater(service)
    if backend is None:
        raise RuntimeError("Sparkle probe must run inside the frozen app")
    if backend.updater.automaticallyChecksForUpdates():
        raise AssertionError("Saved automatic-check opt-out did not override the bundle default before startup")
    helper = ctypes.CDLL(str(Path(bundle.privateFrameworksPath()) / "probe.dylib"))
    helper.probe_channels.argtypes = [ctypes.c_void_p]
    helper.probe_postpone.argtypes = [ctypes.c_void_p]
    delegate = objc.pyobjc_id(backend.delegate)
    try:
        preview = helper.probe_channels(delegate)
        windows: list[str] = []
        if feed_check:
            service.check()
            deadline = time.monotonic() + 20
            while service.status.phase == "checking" and time.monotonic() < deadline:
                foundation.NSRunLoop.currentRunLoop().runUntilDate_(foundation.NSDate.dateWithTimeIntervalSinceNow_(0.1))
            if service.status.phase != "available":
                raise AssertionError(f"HTTPS preview update check failed: {service.status.model_dump_json()}")
            foundation.NSRunLoop.currentRunLoop().runUntilDate_(foundation.NSDate.dateWithTimeIntervalSinceNow_(1))
            windows = [str(window.title()) for window in appkit.NSApplication.sharedApplication().windows() if window.isVisible()]
            if not windows:
                raise AssertionError("Sparkle's standard update UI was not displayed")
        service.configure("release", False)
        release = helper.probe_channels(delegate)
        exercise_preferences(service)
        postponed = bool(helper.probe_postpone(delegate))
        if not service.resume_install() or service.resume_install():
            raise AssertionError("Deferred native continuation must run exactly once")
        failures: list[bool] = []

        def failed_install() -> None:
            failures.append(True)
            raise RuntimeError("synthetic native-timer installation failure")

        service.defer_install(failed_install)
        foundation.NSRunLoop.currentRunLoop().runUntilDate_(foundation.NSDate.dateWithTimeIntervalSinceNow_(1.1))
        if failures != [True] or service.status.phase != "error":
            raise AssertionError("Cocoa timer did not recover from the install failure exactly once")
        report = ProbeReport(frozen=bool(getattr(sys, "frozen", False)), controller=backend.controller.className(),
                             build=service.status.build, preview_channels=preview, release_channels=release,
                             postponed=postponed, continuation_calls=helper.probe_installs(),
                             https_check=feed_check, standard_ui_windows=windows, preferences_ui=True,
                             automatic_checks=bool(backend.updater.automaticallyChecksForUpdates()),
                             continuation_error_recovered=True)
        if (preview, release, postponed, report.continuation_calls) != (1, 0, True, 1):
            raise AssertionError(report.model_dump_json())
        print(report.model_dump_json(indent=2))
    finally:
        backend.close()
        foundation.NSUserDefaults.standardUserDefaults().removePersistentDomainForName_(bundle.bundleIdentifier())


def freeze(feed_check: bool) -> None:
    """Build a fixture-only app; no ClamAV, source mailbox, or real preferences are used."""
    from sparkle_bundle import bundle_sparkle  # pylint: disable=import-outside-toplevel

    work = ROOT / ".tmp/sparkle-probe"
    work.mkdir(parents=True, exist_ok=True)
    executable_name = "Sparkle Probe"
    with (work / "build.log").open("w", encoding="utf-8") as log:
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed", "--onedir",
                        "--name", executable_name, "--osx-bundle-identifier", f"net.simson.sparkle-probe.{uuid4().hex}",
                        "--distpath", str(work / "dist"), "--workpath", str(work / "build"), "--specpath", str(work),
                        "--hidden-import", "Foundation", "--hidden-import", "AppKit", "--hidden-import", "objc",
                        str(Path(__file__).resolve())], check=True, stdout=log, stderr=subprocess.STDOUT)
    app = work / "dist" / f"{executable_name}.app"
    plist = app / "Contents/Info.plist"
    with plist.open("rb") as handle:
        info = plistlib.load(handle)
    feed_key, public_key, version_key = "SUFeedURL", "SUPublicEDKey", "CFBundleVersion"
    checks_key, automatic_key, allows_key = "SUEnableAutomaticChecks", "SUAutomaticallyUpdate", "SUAllowsAutomaticUpdates"
    info[feed_key], info[public_key], info[version_key] = SPARKLE_FEED_URL, SPARKLE_PUBLIC_KEY, "1000000101"
    info[checks_key] = True
    info[automatic_key] = info[allows_key] = False
    with plist.open("wb") as handle:
        plistlib.dump(info, handle)
    bundle_sparkle(app, ROOT / ".tools/sparkle" / SPARKLE_VERSION, "-")
    source = work / "probe.m"
    source.write_text(PROBE_SOURCE, encoding="utf-8")
    helper = app / "Contents/Frameworks/probe.dylib"
    subprocess.run(["cc", "-dynamiclib", "-fblocks", "-framework", "Foundation", str(source), "-o", str(helper)], check=True)
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--options", "0", str(app)], check=True)
    result = subprocess.run([str(app / "Contents/MacOS" / executable_name), "--run", *(["--feed-check"] if feed_check else [])],
                            check=False, timeout=40, capture_output=True, text=True)
    (work / "report.json").write_text(result.stdout, encoding="utf-8")
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    result.check_returncode()
    print(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--feed-check", action="store_true", help="exercise a manual HTTPS check against the public feed")
    args = parser.parse_args()
    if args.run:
        exercise(args.feed_check)
    else:
        freeze(args.feed_check)


if __name__ == "__main__":
    main()
