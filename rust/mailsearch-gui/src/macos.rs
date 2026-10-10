// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Supply macOS document selection and application identity for the Rust desktop.
// Archive packages stay documents while ordinary archive directories remain selectable.
// Dialog work is marshalled to AppKit's main thread from the archive worker.
// Bundle resources supply the Dock name/icon without modifying machine preferences.
// The archive-opening worker still validates selected paths and handles recovery.
use anyhow::{Context, Result};
use objc2::{AnyThread, MainThreadMarker};
use objc2_app_kit::{NSApplication, NSImage, NSModalResponseOK, NSOpenPanel};
use objc2_foundation::{NSBundle, NSProcessInfo, NSString};
use std::path::PathBuf;

pub fn pick_archive() -> Option<PathBuf> {
    dispatch2::run_on_main(|marker| {
        let panel = archive_panel(marker);
        if panel.runModal() != NSModalResponseOK {
            return None;
        }
        panel
            .URL()?
            .path()
            .map(|path| PathBuf::from(path.to_string()))
    })
}

pub fn archive_panel(marker: MainThreadMarker) -> objc2::rc::Retained<NSOpenPanel> {
    let panel = NSOpenPanel::openPanel(marker);
    panel.setTitle(Some(&NSString::from_str("Open email archive")));
    panel.setCanChooseFiles(true);
    panel.setCanChooseDirectories(true);
    panel.setTreatsFilePackagesAsDirectories(false);
    panel.setAllowsMultipleSelection(false);
    panel.setCanCreateDirectories(false);
    panel
}

pub fn configure_identity() -> Result<bool> {
    let marker =
        MainThreadMarker::new().context("Application identity requires the main thread")?;
    let bundle = NSBundle::mainBundle();
    if let Some(value) =
        bundle.objectForInfoDictionaryKey(&NSString::from_str("CFBundleDisplayName"))
    {
        if let Some(name) = value.downcast_ref::<NSString>() {
            NSProcessInfo::processInfo().setProcessName(name);
        }
    }
    if let Some(path) = bundle.pathForResource_ofType(
        Some(&NSString::from_str("MailArchiver")),
        Some(&NSString::from_str("icns")),
    ) {
        let image = NSImage::initWithContentsOfFile(NSImage::alloc(), &path)
            .context("Bundled application icon could not be loaded")?;
        // The image is valid and AppKit retains it; all work stays on the main thread.
        unsafe {
            NSApplication::sharedApplication(marker).setApplicationIconImage(Some(&image));
        }
        return Ok(true);
    }
    Ok(false)
}

pub fn inspect() -> Result<serde_json::Value> {
    use objc2_app_kit::NSApplicationActivationPolicy;
    let marker = MainThreadMarker::new().context("Native inspection requires the main thread")?;
    // Inspect actual AppKit objects without a modal panel, event loop or Dock tile.
    anyhow::ensure!(
        NSApplication::sharedApplication(marker)
            .setActivationPolicy(NSApplicationActivationPolicy::Prohibited),
        "Could not suppress the inspection process Dock tile"
    );
    let icon = configure_identity()?;
    let panel = archive_panel(marker);
    Ok(
        serde_json::json!({"choose_files":panel.canChooseFiles(),"choose_directories":panel.canChooseDirectories(),
        "packages_as_directories":panel.treatsFilePackagesAsDirectories(),"multiple":panel.allowsMultipleSelection(),
        "create_directories":panel.canCreateDirectories(),"bundle_icon_loaded":icon,
        "process_name":NSProcessInfo::processInfo().processName().to_string()}),
    )
}

// Defer Cocoa termination too: Sparkle and the application menu call terminate:
// directly, whereas the Rust window/menu actions use the event-loop Quit event.
static TERMINATION: std::sync::OnceLock<Box<dyn Fn() + Send + Sync>> = std::sync::OnceLock::new();
static TERMINATION_REPLY_QUEUED: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);
static TERMINATION_ALLOWED: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);
static TERMINATION_PENDING: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

unsafe extern "C-unwind" fn should_terminate(
    _delegate: &objc2::runtime::AnyObject,
    _selector: objc2::runtime::Sel,
    _application: &NSApplication,
) -> objc2_app_kit::NSApplicationTerminateReply {
    TERMINATION_PENDING.store(true, std::sync::atomic::Ordering::Release);
    if let Some(notify) = TERMINATION.get() {
        notify();
    }
    objc2_app_kit::NSApplicationTerminateReply::TerminateLater
}

pub fn coordinate_termination(notify: impl Fn() + Send + Sync + 'static) -> Result<()> {
    use objc2::{
        runtime::{AnyClass, ClassBuilder},
        sel,
    };
    let marker = MainThreadMarker::new().context("Termination requires the main thread")?;
    let application = NSApplication::sharedApplication(marker);
    let delegate = application
        .delegate()
        .context("Missing Tao application delegate")?;
    let object: &objc2::runtime::AnyObject = (*delegate).as_ref();
    let host = object.class();
    anyhow::ensure!(
        host.name().to_bytes() == b"TaoAppDelegateParent",
        "Unexpected application delegate"
    );
    anyhow::ensure!(
        host.instance_method(sel!(applicationShouldTerminate:))
            .is_none(),
        "Existing application termination policy must be preserved"
    );
    TERMINATION
        .set(Box::new(notify))
        .map_err(|_| anyhow::anyhow!("Termination policy already registered"))?;
    let mut signatures = ClassBuilder::new(c"ECTTerminationMethods", host)
        .context("Register termination signature")?;
    unsafe {
        signatures.add_method(
            sel!(applicationShouldTerminate:),
            should_terminate as unsafe extern "C-unwind" fn(_, _, _) -> _,
        );
    }
    let signatures = signatures.register();
    let method = signatures
        .instance_method(sel!(applicationShouldTerminate:))
        .context("Missing termination signature")?;
    anyhow::ensure!(
        unsafe {
            objc2::ffi::class_addMethod(
                host as *const AnyClass as *mut AnyClass,
                sel!(applicationShouldTerminate:),
                method.implementation(),
                objc2::ffi::method_getTypeEncoding(method),
            )
            .as_bool()
        },
        "Could not add termination policy"
    );
    Ok(())
}

pub fn finish_termination(allow: bool) -> bool {
    use std::sync::atomic::Ordering;
    if !TERMINATION_PENDING.load(Ordering::Acquire) {
        return false;
    }
    TERMINATION_ALLOWED.store(allow, Ordering::Release);
    if !TERMINATION_REPLY_QUEUED.swap(true, Ordering::AcqRel) {
        // Cocoa sends applicationWillTerminate synchronously. Tao's event
        // callback holds its handler mutex, so reply only after it returns.
        dispatch2::DispatchQueue::main().exec_async(|| {
            if let Some(marker) = MainThreadMarker::new() {
                let allow = TERMINATION_ALLOWED.load(Ordering::Acquire);
                TERMINATION_PENDING.store(false, Ordering::Release);
                NSApplication::sharedApplication(marker).replyToApplicationShouldTerminate(allow);
                TERMINATION_REPLY_QUEUED.store(false, Ordering::Release);
            }
        });
    }
    true
}
