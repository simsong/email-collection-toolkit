// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Adapt the packaged Sparkle framework to the Rust desktop on Cocoa's main thread.
// Keep the standard native controller, delegate and timer alive together.
// A copied installation block waits without a deadline for the shared writer fence.
// Local failures restore the reader but retain any external staged-installer hazard.
// Confirmed native cancellation clears staging; every later Quit still takes the fence.
// Packaging metadata supplies the existing update identity and public signing key.
// Source launches never load a framework or start automatic network checks.
use crate::update_policy::{Channel, Configuration, Installation};
use anyhow::{ensure, Context, Result};
use block2::{Block, RcBlock};
use objc2::{
    define_class, msg_send,
    rc::Retained,
    runtime::{AnyClass, AnyObject, Bool},
    DefinedClass, MainThreadMarker, MainThreadOnly,
};
use objc2_foundation::{NSBundle, NSError, NSObject, NSObjectProtocol, NSSet, NSString, NSTimer};
use std::{
    cell::{Cell, RefCell},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
};

struct DelegateState {
    installation: Arc<Installation>,
    pending: RefCell<Option<RcBlock<dyn Fn()>>>,
    channel: Cell<Channel>,
    ready: AtomicBool,
    closing: AtomicBool,
    staged: Cell<bool>,
    relaunch: Cell<bool>,
    skipped: Cell<bool>,
    failure: RefCell<Option<String>>,
    notify: Box<dyn Fn() + Send + Sync>,
}

define_class!(
    #[unsafe(super = NSObject)]
    #[thread_kind = MainThreadOnly]
    #[ivars = DelegateState]
    struct ECTRustSparkleDelegate;
    unsafe impl NSObjectProtocol for ECTRustSparkleDelegate {}
    impl ECTRustSparkleDelegate {
        #[unsafe(method_id(allowedChannelsForUpdater:))]
        fn channels(&self, _updater: &AnyObject) -> Retained<NSSet<NSString>> {
            if self.ivars().channel.get() == Channel::Preview {
                NSSet::from_slice(&[&*NSString::from_str("preview")])
            } else {
                NSSet::from_slice(&[])
            }
        }

        #[unsafe(method(updater:willExtractUpdate:))]
        fn extracting(&self, _updater: &AnyObject, _item: &AnyObject) {
            // Sparkle's external installer may install on application exit even
            // before a user asks for relaunch. Track that hazard before launch.
            self.ivars().staged.set(true);
            self.ivars().skipped.set(false);
        }

        #[unsafe(method(standardUserDriverWillHandleShowingUpdate:forUpdate:state:))]
        fn showing(&self, _show: Bool, _item: &AnyObject, state: &AnyObject) {
            // Public SPUUserUpdateStageInstalling = 2 (NSInteger), including a
            // resumed staged session that did not extract in this process.
            let stage: isize = unsafe { msg_send![state, stage] };
            if stage == 2 { self.ivars().staged.set(true); }
        }

        #[unsafe(method(updater:willInstallUpdate:))]
        fn installing(&self, _updater: &AnyObject, _item: &AnyObject) {
            self.ivars().staged.set(true);
        }

        #[unsafe(method(updater:userDidMakeChoice:forUpdate:state:))]
        fn choice(&self, _updater: &AnyObject, choice: isize, _item: &AnyObject, _state: &AnyObject) {
            // SPUUserUpdateChoiceSkip = 0 (NSInteger). Wait for cycle completion:
            // the SDK still has to cancel its external installer after this call.
            self.ivars().skipped.set(choice == 0);
        }

        #[unsafe(method(updater:shouldPostponeRelaunchForUpdate:untilInvokingBlock:))]
        fn postpone(&self, _updater: &AnyObject, _item: &AnyObject, handler: &Block<dyn Fn()>) -> Bool {
            self.ivars().staged.set(true);
            self.ivars().relaunch.set(true);
            self.ivars().closing.store(true, Ordering::Release);
            self.ivars().ready.store(false, Ordering::Release);
            self.ivars().failure.borrow_mut().take();
            *self.ivars().pending.borrow_mut() = Some(handler.copy());
            (self.ivars().notify)();
            Bool::YES
        }

        #[unsafe(method(ectResumeUpdate:))]
        fn resume(&self, _timer: &NSTimer) {
            if !self.ivars().staged.get() || !self.ivars().ready.load(Ordering::Acquire) || self.ivars().installation.is_installing() { return; }
            match self.ivars().installation.reserve() {
                Ok(true) => {
                    // Remove before invoking native code, allowing reentrant failure
                    // callbacks to clear the fence without borrowing this RefCell.
                    let continuation = self.ivars().pending.borrow_mut().take();
                    if let Some(continuation) = continuation {
                        if let Err(error) = self.ivars().installation.invoke(&continuation) {
                            self.fail(format!("{error:#}"));
                        }
                    } else {
                        // No relaunch callback is needed for installation on Quit.
                        // Hold the fence and wake Rust to permit ordinary termination.
                        self.ivars().installation.installing();
                        (self.ivars().notify)();
                    }
                }
                Ok(false) => (),
                Err(error) => self.fail(format!("Update installation failed: {error:#}")),
            }
        }

        #[unsafe(method(updater:didFinishUpdateCycleForUpdateCheck:error:))]
        fn finished(&self, _updater: &AnyObject, _check: usize, error: Option<&NSError>) {
            let skipped = self.ivars().skipped.replace(false);
            if let Some(error) = error {
                if self.ivars().staged.get() { self.canceled(error.localizedDescription().to_string()); }
            } else if skipped && self.ivars().staged.get() {
                self.canceled("Update skipped.".into());
            }
        }
    }
);

impl ECTRustSparkleDelegate {
    fn waiting(&self) -> bool {
        self.ivars().staged.get()
            && (!self.ivars().installation.is_installing() || self.ivars().relaunch.get())
    }
    fn fail(&self, message: String) {
        self.ivars().pending.borrow_mut().take();
        self.ivars().installation.cancel();
        self.ivars().relaunch.set(false);
        self.ivars().skipped.set(false);
        self.ivars().ready.store(false, Ordering::Release);
        if self.ivars().closing.load(Ordering::Acquire) {
            *self.ivars().failure.borrow_mut() = Some(message);
            (self.ivars().notify)();
        }
    }
    fn canceled(&self, message: String) {
        // Only SDK cycle completion confirms its external installer is canceled.
        // A local cleanup/reservation failure cannot make that guarantee.
        self.ivars().staged.set(false);
        self.fail(message);
    }
}

pub struct MacSparkle {
    controller: Retained<AnyObject>,
    _delegate: Retained<ECTRustSparkleDelegate>,
    timer: Retained<NSTimer>,
    _installation: Arc<Installation>,
}

impl MacSparkle {
    pub fn load(
        configuration: &Configuration,
        automatic: bool,
        channel: Channel,
        inspect: bool,
        notify: impl Fn() + Send + Sync + 'static,
    ) -> Result<Self> {
        let marker = MainThreadMarker::new().context("Sparkle requires the Cocoa main thread")?;
        let bundle = NSBundle::mainBundle();
        // Inspection uses Cocoa's command-line argument domain, never setters.
        // Retain a snapshot to detect even an unexpected SDK defaults write.
        let defaults_class = AnyClass::get(c"NSUserDefaults").context("Missing Cocoa defaults")?;
        let defaults: Retained<AnyObject> =
            unsafe { msg_send![defaults_class, standardUserDefaults] };
        let identifier = bundle
            .bundleIdentifier()
            .context("Missing bundle identity")?;
        let before: Option<Retained<AnyObject>> =
            unsafe { msg_send![&*defaults, persistentDomainForName: &*identifier] };
        if inspect {
            // Volatile argument defaults override the SDK's first-launch and
            // automatic-check behavior without touching the persistent domain.
            let domain = NSString::from_str("NSArgumentDomain");
            let arguments: Retained<AnyObject> =
                unsafe { msg_send![&*defaults, volatileDomainForName: &*domain] };
            let arguments: Retained<AnyObject> = unsafe { msg_send![&*arguments, mutableCopy] };
            for (key, value) in [
                ("SUEnableAutomaticChecks", "NO"),
                ("SUAutomaticallyUpdate", "NO"),
                ("SUHasLaunchedBefore", "YES"),
            ] {
                unsafe {
                    let _: () = msg_send![&*arguments, setObject: &*NSString::from_str(value), forKey: &*NSString::from_str(key)];
                }
            }
            unsafe {
                let _: () =
                    msg_send![&*defaults, setVolatileDomain: &*arguments, forName: &*domain];
            }
        }
        for (name, expected) in [
            ("SUFeedURL", configuration.feed),
            ("SUPublicEDKey", configuration.key),
            ("CFBundleVersion", configuration.build),
            ("CFBundleShortVersionString", env!("ECT_APP_VERSION")),
        ] {
            let value = bundle
                .objectForInfoDictionaryKey(&NSString::from_str(name))
                .context("Missing packaged updater metadata")?;
            ensure!(
                value
                    .downcast_ref::<NSString>()
                    .is_some_and(|value| value.to_string() == expected),
                "Packaged updater metadata differs from the release mapper: {name}"
            );
        }
        let path = bundle
            .privateFrameworksPath()
            .context("No packaged frameworks directory")?;
        let path = NSString::from_str(&format!("{path}/Sparkle.framework"));
        let framework =
            NSBundle::bundleWithPath(&path).context("Bundled Sparkle framework is missing")?;
        // SAFETY: load only the packaging-validated, signed sibling framework.
        ensure!(
            unsafe { framework.load() },
            "Bundled Sparkle framework could not load"
        );
        let class = AnyClass::get(c"SPUStandardUpdaterController")
            .context("Sparkle controller is unavailable")?;
        let installation = Arc::new(Installation::default());
        let delegate = ECTRustSparkleDelegate::alloc(marker).set_ivars(DelegateState {
            installation: installation.clone(),
            pending: RefCell::new(None),
            channel: Cell::new(channel),
            ready: AtomicBool::new(false),
            closing: AtomicBool::new(false),
            staged: Cell::new(false),
            relaunch: Cell::new(false),
            skipped: Cell::new(false),
            failure: RefCell::new(None),
            notify: Box::new(notify),
        });
        // SAFETY: NSObject init and the pinned Sparkle 2 controller selectors use
        // the declared Cocoa object/BOOL/error-pointer ABIs on the main thread.
        let delegate: Retained<ECTRustSparkleDelegate> =
            unsafe { msg_send![super(delegate), init] };
        let controller: objc2::rc::Allocated<AnyObject> = unsafe { msg_send![class, alloc] };
        let controller: Retained<AnyObject> = unsafe {
            msg_send![controller,
            initWithStartingUpdater: Bool::NO, updaterDelegate: &*delegate,
            userDriverDelegate: &*delegate]
        };
        let updater: Retained<AnyObject> = unsafe { msg_send![&*controller, updater] };
        if !inspect {
            unsafe {
                let _: () =
                    msg_send![&*updater, setAutomaticallyChecksForUpdates: Bool::from(automatic)];
                let _: () = msg_send![&*updater, setAutomaticallyDownloadsUpdates: Bool::NO];
                let _: () = msg_send![&*updater, setUpdateCheckInterval: crate::update_policy::DAILY_SECONDS as f64];
            }
        }
        let mut error: *mut NSError = std::ptr::null_mut();
        let started: Bool = unsafe { msg_send![&*updater, startUpdater: &mut error] };
        ensure!(
            started.as_bool(),
            "Sparkle could not start: {}",
            unsafe { error.as_ref() }
                .map(|error| error.localizedDescription().to_string())
                .unwrap_or_default()
        );
        if inspect {
            // Let the SDK perform its scheduled first-launch cycle under the
            // volatile overrides, then compare the real persistent domain.
            objc2_foundation::NSRunLoop::currentRunLoop()
                .runUntilDate(&objc2_foundation::NSDate::dateWithTimeIntervalSinceNow(0.1));
            let after: Option<Retained<AnyObject>> =
                unsafe { msg_send![&*defaults, persistentDomainForName: &*identifier] };
            let unchanged = match (&before, &after) {
                (None, None) => true,
                (Some(before), Some(after)) => unsafe {
                    let equal: Bool = msg_send![&**before, isEqual: &**after];
                    equal.as_bool()
                },
                _ => false,
            };
            ensure!(unchanged, "Updater inspection changed persistent defaults");
        }
        let timer = unsafe {
            NSTimer::timerWithTimeInterval_target_selector_userInfo_repeats(
                0.5,
                &delegate,
                objc2::sel!(ectResumeUpdate:),
                None,
                true,
            )
        };
        // Continue fence polling while Cocoa is inside a modal update/quit loop.
        unsafe {
            objc2_foundation::NSRunLoop::currentRunLoop()
                .addTimer_forMode(&timer, objc2_foundation::NSRunLoopCommonModes);
        }
        Ok(Self {
            controller,
            _delegate: delegate,
            timer,
            _installation: installation,
        })
    }

    pub fn waiting(&self) -> bool {
        self._delegate.waiting()
    }
    pub fn installing(&self) -> bool {
        self._installation.is_installing()
    }
    pub fn begin_shutdown(&self) {
        self._delegate
            .ivars()
            .closing
            .store(true, Ordering::Release);
    }
    pub fn shutdown_ready(&self, ready: bool) {
        self._delegate.ivars().ready.store(ready, Ordering::Release);
        if !ready {
            self._delegate
                .ivars()
                .closing
                .store(false, Ordering::Release);
        }
    }
    pub fn take_failure(&self) -> Option<String> {
        self._delegate.ivars().failure.borrow_mut().take()
    }
    pub fn cancel(&self, message: &str) {
        self._delegate.fail(message.into());
    }

    pub fn configure(&self, automatic: bool, channel: Channel) {
        let channel_changed = self._delegate.ivars().channel.replace(channel) != channel;
        // SAFETY: retained standard controller/updater; caller is the GUI thread.
        unsafe {
            let updater: Retained<AnyObject> = msg_send![&*self.controller, updater];
            let current: Bool = msg_send![&*updater, automaticallyChecksForUpdates];
            if current.as_bool() != automatic || channel_changed {
                let _: () =
                    msg_send![&*updater, setAutomaticallyChecksForUpdates: Bool::from(automatic)];
                let _: () = msg_send![&*updater, resetUpdateCycleAfterShortDelay];
            }
        }
    }

    pub fn check(&self) {
        // SAFETY: Sparkle owns the confirmation UI, download and verification.
        unsafe {
            let _: () =
                msg_send![&*self.controller, checkForUpdates: std::ptr::null::<AnyObject>()];
        }
    }
}

impl Drop for MacSparkle {
    fn drop(&mut self) {
        self.timer.invalidate();
        // Retain any installation reservation through native relaunch/process exit.
        // The standard controller owns its update session and installation helpers.
    }
}

pub fn inspect_shutdown() -> Result<()> {
    // Exercise the actual Objective-C delegate and copied block without loading
    // an updater, making a network request, presenting UI or replacing an app.
    use std::io::{self, BufRead, Write};
    use std::sync::atomic::AtomicUsize;
    use tao::platform::macos::{ActivationPolicy, EventLoopExtMacOS};
    let mut events = tao::event_loop::EventLoopBuilder::<()>::new().build();
    events.set_activation_policy(ActivationPolicy::Prohibited);
    events.set_dock_visibility(false);
    events.set_activate_ignoring_other_apps(false);
    objc2_app_kit::NSApplication::sharedApplication(
        MainThreadMarker::new().context("Missing main thread")?,
    )
    .setActivationPolicy(objc2_app_kit::NSApplicationActivationPolicy::Prohibited);
    let notifications = Arc::new(AtomicUsize::new(0));
    let termination = notifications.clone();
    crate::macos::coordinate_termination(move || {
        termination.fetch_add(1, Ordering::AcqRel);
        crate::macos::finish_termination(false);
    })?;
    let marker = MainThreadMarker::new().context("Shutdown probe requires the main thread")?;
    let state = notifications.clone();
    let installation = Arc::new(Installation::default());
    let delegate = ECTRustSparkleDelegate::alloc(marker).set_ivars(DelegateState {
        installation: installation.clone(),
        pending: RefCell::new(None),
        channel: Cell::new(Channel::default()),
        ready: AtomicBool::new(false),
        closing: AtomicBool::new(false),
        staged: Cell::new(false),
        relaunch: Cell::new(false),
        skipped: Cell::new(false),
        failure: RefCell::new(None),
        notify: Box::new(move || {
            state.fetch_add(1, Ordering::AcqRel);
        }),
    });
    let delegate: Retained<ECTRustSparkleDelegate> = unsafe { msg_send![super(delegate), init] };
    let calls = Arc::new(AtomicUsize::new(0));
    let counter = calls.clone();
    let continuation: RcBlock<dyn Fn()> = RcBlock::new(move || {
        counter.fetch_add(1, Ordering::AcqRel);
    });
    let object = NSObject::new();
    let postponed: Bool = unsafe {
        msg_send![&*delegate, updater: &*object, shouldPostponeRelaunchForUpdate: &*object, untilInvokingBlock: &*continuation]
    };
    ensure!(
        postponed.as_bool() && notifications.load(Ordering::Acquire) == 1,
        "Installation did not request coordinated shutdown"
    );
    println!("waiting");
    io::stdout().flush()?;
    // Same selector used by the retained production NSTimer. Its argument is
    // deliberately ignored; calling the Objective-C method exercises its ABI.
    for line in io::stdin().lock().lines() {
        let command = line?;
        match command.as_str() {
            "stage" => {
                delegate.ivars().closing.store(false, Ordering::Release);
                delegate.ivars().failure.borrow_mut().take();
                unsafe {
                    let _: () =
                        msg_send![&*delegate, updater: &*object, willExtractUpdate: &*object];
                }
            }
            "ready" => {
                delegate.ivars().closing.store(true, Ordering::Release);
                delegate.ivars().ready.store(true, Ordering::Release);
            }
            "attempt" => unsafe {
                let _: () = msg_send![&*delegate, ectResumeUpdate: std::ptr::null::<NSTimer>()];
            },
            "skip" | "dismiss" => {
                let choice = if command == "skip" { 0isize } else { 2isize };
                unsafe {
                    let _: () = msg_send![&*delegate, updater: &*object, userDidMakeChoice: choice, forUpdate: &*object, state: &*object];
                }
            }
            "complete" => unsafe {
                let _: () = msg_send![&*delegate, updater: &*object, didFinishUpdateCycleForUpdateCheck: 0usize, error: std::ptr::null::<NSError>()];
            },
            "local-fail" => delegate.fail("Local export cleanup failed.".into()),
            "fail" => {
                let class = AnyClass::get(c"NSError").context("Missing NSError")?;
                let error: Retained<NSError> = unsafe {
                    msg_send![class, errorWithDomain: &*NSString::from_str("ECTShutdownProbe"), code: 42isize, userInfo: std::ptr::null::<AnyObject>()]
                };
                unsafe {
                    let _: () = msg_send![&*delegate, updater: &*object, didFinishUpdateCycleForUpdateCheck: 0usize, error: &*error];
                }
                ensure!(
                    !delegate.waiting() && delegate.ivars().failure.borrow().is_some(),
                    "Native failure left installation pending"
                );
            }
            "quit" => {
                let before = notifications.load(Ordering::Acquire);
                objc2_app_kit::NSApplication::sharedApplication(marker).terminate(None);
                ensure!(
                    notifications.load(Ordering::Acquire) == before + 1,
                    "Cocoa Quit bypassed the shutdown callback"
                );
                crate::macos::finish_termination(false);
                println!("quit-canceled");
                io::stdout().flush()?;
                continue;
            }
            "done" => break,
            _ => anyhow::bail!("Unknown shutdown probe command"),
        }
        println!(
            "{}:{}",
            if installation.is_installing() {
                if delegate.waiting() {
                    "installing"
                } else {
                    "reserved-for-quit"
                }
            } else if delegate.waiting() {
                "waiting"
            } else {
                "canceled"
            },
            calls.load(Ordering::Acquire)
        );
        io::stdout().flush()?;
    }
    installation.cancel();
    Ok(())
}
