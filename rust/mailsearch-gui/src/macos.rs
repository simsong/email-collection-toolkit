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
