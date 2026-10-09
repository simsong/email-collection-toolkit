// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Capture the actual WKWebView after the native smoke driver verifies its UI.
// WebKit snapshots render the application's view, not a different browser.
// The image is converted to PNG and written only to the explicit test output.
// Completion returns through Tao so failure sets the application's exit code.
// This module is feature-gated and absent from ordinary application builds.
// The caller must keep its native event loop alive until the callback finishes.
use anyhow::{Context, Result};
use block2::RcBlock;
use objc2::AnyThread;
use objc2_app_kit::{NSBitmapImageFileType, NSBitmapImageRep, NSImage};
use objc2_foundation::{NSDictionary, NSError, NSURL};
use std::path::PathBuf;
use wry::{WebView, WebViewExtMacOS};

pub fn snapshot(view: &WebView, output: PathBuf, complete: impl Fn(Result<()>) + 'static) {
    let callback = RcBlock::new(move |image: *mut NSImage, error: *mut NSError| {
        let result = (|| {
            // WebKit owns these callback arguments and keeps them alive for this call.
            if let Some(error) = unsafe { error.as_ref() } {
                anyhow::bail!("WebKit snapshot: {error}");
            }
            let image = unsafe { image.as_ref() }.context("WebKit returned no snapshot")?;
            let tiff = image
                .TIFFRepresentation()
                .context("Snapshot has no image data")?;
            let bitmap = NSBitmapImageRep::initWithData(NSBitmapImageRep::alloc(), &tiff)
                .context("Cannot decode snapshot")?;
            // Empty properties is valid for PNG; no untyped property values are supplied.
            let png = unsafe {
                bitmap.representationUsingType_properties(
                    NSBitmapImageFileType::PNG,
                    &NSDictionary::new(),
                )
            }
            .context("Cannot encode snapshot PNG")?;
            let url = NSURL::from_file_path(&output).context("Invalid snapshot path")?;
            anyhow::ensure!(
                png.writeToURL_atomically(&url, false),
                "Cannot write snapshot PNG"
            );
            println!(
                "Native GUI search and message display passed; screenshot: {}",
                output.display()
            );
            Ok(())
        })();
        complete(result);
    });
    // Called on the GUI thread with a live WKWebView; WebKit retains the block.
    unsafe {
        view.webview()
            .takeSnapshotWithConfiguration_completionHandler(None, &callback);
    }
}
