// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Adapt the packaged Sparkle framework to the Rust desktop on Cocoa's main thread.
// Keep the standard native controller, delegate and timer alive together.
// A copied installation block waits without a deadline for the shared writer fence.
// Native failure callbacks release the reservation and discard the continuation.
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
    sync::Arc,
};

struct DelegateState {
    installation: Arc<Installation>,
    pending: RefCell<Option<RcBlock<dyn Fn()>>>,
    channel: Cell<Channel>,
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

        #[unsafe(method(updater:shouldPostponeRelaunchForUpdate:untilInvokingBlock:))]
        fn postpone(&self, _updater: &AnyObject, _item: &AnyObject, handler: &Block<dyn Fn()>) -> Bool {
            *self.ivars().pending.borrow_mut() = Some(handler.copy());
            Bool::YES
        }

        #[unsafe(method(ectResumeUpdate:))]
        fn resume(&self, _timer: &NSTimer) {
            if self.ivars().pending.borrow().is_none() { return; }
            match self.ivars().installation.reserve() {
                Ok(true) => {
                    // Remove before invoking native code, allowing reentrant failure
                    // callbacks to clear the fence without borrowing this RefCell.
                    let continuation = self.ivars().pending.borrow_mut().take();
                    if let Some(continuation) = continuation {
                        if let Err(error) = self.ivars().installation.invoke(&continuation) {
                            eprintln!("{error:#}");
                        }
                    }
                }
                Ok(false) => (),
                Err(error) => eprintln!("Update installation is deferred: {error:#}"),
            }
        }

        #[unsafe(method(updater:didFinishUpdateCycleForUpdateCheck:error:))]
        fn finished(&self, _updater: &AnyObject, _check: usize, error: Option<&NSError>) {
            if error.is_some_and(|error| error.code() != 1001) {
                self.ivars().pending.borrow_mut().take();
                self.ivars().installation.cancel();
            }
        }
    }
);

pub struct MacSparkle {
    controller: Retained<AnyObject>,
    _delegate: Retained<ECTRustSparkleDelegate>,
    timer: Retained<NSTimer>,
    _installation: Arc<Installation>,
}

impl MacSparkle {
    pub fn load(configuration: &Configuration, automatic: bool, channel: Channel) -> Result<Self> {
        let marker = MainThreadMarker::new().context("Sparkle requires the Cocoa main thread")?;
        let bundle = NSBundle::mainBundle();
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
        });
        // SAFETY: NSObject init and the pinned Sparkle 2 controller selectors use
        // the declared Cocoa object/BOOL/error-pointer ABIs on the main thread.
        let delegate: Retained<ECTRustSparkleDelegate> =
            unsafe { msg_send![super(delegate), init] };
        let controller: objc2::rc::Allocated<AnyObject> = unsafe { msg_send![class, alloc] };
        let controller: Retained<AnyObject> = unsafe {
            msg_send![controller,
            initWithStartingUpdater: Bool::NO, updaterDelegate: &*delegate,
            userDriverDelegate: std::ptr::null::<AnyObject>()]
        };
        let updater: Retained<AnyObject> = unsafe { msg_send![&*controller, updater] };
        unsafe {
            let _: () =
                msg_send![&*updater, setAutomaticallyChecksForUpdates: Bool::from(automatic)];
            let _: () = msg_send![&*updater, setAutomaticallyDownloadsUpdates: Bool::NO];
            let _: () = msg_send![&*updater, setUpdateCheckInterval: crate::update_policy::DAILY_SECONDS as f64];
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
        let timer = unsafe {
            NSTimer::scheduledTimerWithTimeInterval_target_selector_userInfo_repeats(
                0.5,
                &delegate,
                objc2::sel!(ectResumeUpdate:),
                None,
                true,
            )
        };
        Ok(Self {
            controller,
            _delegate: delegate,
            timer,
            _installation: installation,
        })
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
