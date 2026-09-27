# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""PyObjC adapter for the pinned Sparkle framework in an installed macOS app."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any
from types import new_class
from collections.abc import Callable

from .updates import UpdateChannel, UpdateService, allowed_channels

NO_UPDATE_ERROR = 1001
ARGUMENTS = "arguments"
CALLABLE = "callable"
RETVAL = "retval"
TYPE = "type"
TYPE_MODIFIER = "type_modifier"
FEED_KEY = "SUFeedURL"
PUBLIC_KEY = "SUPublicEDKey"
BUILD_KEY = "CFBundleVersion"
PROTOCOLS_KEY = "protocols"
MODULE_KEY = "__module__"


def load_framework(path: Path) -> Any:
    """Load the actual framework, registering its Objective-C classes and protocols."""
    foundation = import_module("Foundation")
    objc = import_module("objc")
    if not foundation.NSThread.isMainThread():
        raise RuntimeError("Sparkle must be initialized on Cocoa's main thread")
    bundle = foundation.NSBundle.bundleWithPath_(str(path))
    if bundle is None or not bundle.load():
        raise RuntimeError(f"Could not load bundled Sparkle framework: {path}")
    # Objective-C encodes a block only as @?; PyObjC needs the block's callable ABI.
    block = {RETVAL: {TYPE: b"v"}, ARGUMENTS: {0: {TYPE: b"^v"}}}
    objc.registerMetaDataForSelector(
        b"MCTSparkleDelegate", b"updater:shouldPostponeRelaunchForUpdate:untilInvokingBlock:",
        {ARGUMENTS: {4: {CALLABLE: block}}},
    )
    objc.registerMetaDataForSelector(b"SPUUpdater", b"startUpdater:",
                                     {ARGUMENTS: {2: {TYPE_MODIFIER: b"o"}}})
    return objc


def make_delegate(service: UpdateService, objc: Any) -> Any:
    """Use Sparkle's formal protocol to derive selector signatures, including BOOL."""
    foundation = import_module("Foundation")
    class DelegateMethods:
        def allowedChannelsForUpdater_(self, _updater):
            return foundation.NSSet.setWithArray_(list(allowed_channels(service.status.channel)))

        def updater_didFindValidUpdate_(self, _updater, item):
            service.status.phase = "available"
            service.status.detail = f"Version {item.displayVersionString()} is available."

        def updater_didFinishUpdateCycleForUpdateCheck_error_(self, _updater, _check, error):
            detail = str(error.localizedDescription()) if error is not None and error.code() != NO_UPDATE_ERROR else None
            service.finish_check(detail)

        def updater_shouldPostponeRelaunchForUpdate_untilInvokingBlock_(self, _updater, _item, handler):
            service.defer_install(handler)
            return True

    def native_namespace(namespace: dict[str, object]) -> None:
        namespace[MODULE_KEY] = __name__
        namespace.update((name, method) for name, method in vars(DelegateMethods).items() if not name.startswith("__"))
    # Protocols belong to PyObjC's class-construction API, not Python's ordinary
    # __init_subclass__. Create the native class through that metaclass boundary.
    delegate_class = new_class("MCTSparkleDelegate", (objc.lookUpClass("NSObject"),),
                               {PROTOCOLS_KEY: [objc.protocolNamed("SPUUpdaterDelegate")]}, native_namespace)
    return delegate_class.alloc().init()


class SparkleBackend:
    """Own native objects for the entire application lifetime."""

    def __init__(self, service: UpdateService, framework: Path) -> None:
        objc = load_framework(framework)
        self.service = service
        self.delegate = make_delegate(service, objc)
        self.controller = objc.lookUpClass("SPUStandardUpdaterController").alloc().initWithStartingUpdater_updaterDelegate_userDriverDelegate_(
            False, self.delegate, None,
        )
        self.updater = self.controller.updater()
        success, error = self.updater.startUpdater_(None)
        if not success:
            raise RuntimeError(str(error.localizedDescription()) if error else "Sparkle could not start")
        checked = self.updater.lastUpdateCheckDate()
        if checked is not None:
            service.status.last_checked = datetime.fromtimestamp(float(checked.timeIntervalSince1970()), UTC)
        self.updater.setAutomaticallyDownloadsUpdates_(False)
        def resume(_timer: object) -> None:
            service.resume_install()
        self.timer = import_module("Foundation").NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.5, True, resume,
        )

    def check(self) -> None:
        self.controller.checkForUpdates_(None)

    def configure(self, channel: UpdateChannel, automatic_checks: bool) -> None:
        self.service.status.channel = channel
        self.updater.setAutomaticallyChecksForUpdates_(automatic_checks)
        self.updater.setAutomaticallyDownloadsUpdates_(False)
        self.updater.resetUpdateCycleAfterShortDelay()

    def close(self) -> None:
        self.timer.invalidate()


def start_installed_updater(service: UpdateService) -> SparkleBackend | None:
    """Source-checkout launches never start automatic network checks or touch Sparkle defaults."""
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return None
    foundation = import_module("Foundation")
    bundle = foundation.NSBundle.mainBundle()
    info = bundle.infoDictionary()
    if not info.get(FEED_KEY) or not info.get(PUBLIC_KEY):
        raise RuntimeError("Installed application has no Sparkle feed or public key")
    backend = SparkleBackend(service, Path(bundle.privateFrameworksPath()) / "Sparkle.framework")
    service.attach(backend, str(info[BUILD_KEY]))
    backend.configure(service.status.channel, service.status.automatic_checks)
    return backend


def show_preferences(service: UpdateService, save: Callable[[UpdateChannel, bool], None]) -> None:
    """Native Updates pane; Sparkle owns the subsequent download/install dialogs."""
    appkit = import_module("AppKit")
    status = service.status
    alert = appkit.NSAlert.alloc().init()
    alert.setMessageText_("Preferences — Updates")
    alert.setInformativeText_(f"You are running {status.version} (build {status.build or 'source checkout'}).")
    view = appkit.NSView.alloc().initWithFrame_(((0, 0), (470, 220)))
    channel = appkit.NSPopUpButton.alloc().initWithFrame_pullsDown_(((0, 172), (470, 28)), False)
    channel.addItemsWithTitles_(["Release updates", "Preview updates — alpha, beta, and release"])
    channel.selectItemAtIndex_(int(status.channel == "preview"))
    view.addSubview_(channel)
    automatic = appkit.NSButton.checkboxWithTitle_target_action_("Check for updates automatically (daily)", None, None)
    automatic.setFrame_(((0, 135), (470, 28)))
    automatic.setState_(int(status.automatic_checks))
    view.addSubview_(automatic)
    checked = status.last_checked.astimezone().strftime("%Y-%m-%d %H:%M %Z") if status.last_checked else "Not yet"
    text = appkit.NSTextField.wrappingLabelWithString_(
        "Downloading and installation always require confirmation.\n"
        "Preview builds may contain unfinished features. Your choice is retained across upgrades.\n\n"
        f"Last checked: {checked}\n{status.detail}",
    )
    text.setFrame_(((0, 0), (470, 126)))
    view.addSubview_(text)
    alert.setAccessoryView_(view)
    alert.addButtonWithTitle_("Done")
    check = alert.addButtonWithTitle_("Check for Updates…")
    check.setEnabled_(status.available)
    choice = alert.runModal() - 1000
    selected: UpdateChannel = "preview" if channel.indexOfSelectedItem() == 1 else "release"
    save(selected, bool(automatic.state()))
    service.configure(selected, bool(automatic.state()))
    if choice == 1:
        service.check()
