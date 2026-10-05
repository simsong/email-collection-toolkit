// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Capture the real WebView2 surface after native smoke assertions finish.
// The COM stream writes only to the explicit synthetic test output path.
// Keep the stream alive until WebView2 reports capture completion.
// Completion returns through Tao so capture errors fail the native process.
// This implementation is excluded from ordinary application builds.
// macOS retains its existing independent WKWebView capture implementation.
use anyhow::Result;
use std::{path::PathBuf, rc::Rc};
use webview2_com::{
    CapturePreviewCompletedHandler,
    Microsoft::Web::WebView2::Win32::COREWEBVIEW2_CAPTURE_PREVIEW_IMAGE_FORMAT_PNG,
};
use windows::{
    core::HSTRING,
    Win32::{
        System::Com::{STGM_CREATE, STGM_SHARE_EXCLUSIVE, STGM_WRITE},
        UI::Shell::SHCreateStreamOnFileEx,
    },
};
use wry::{WebView, WebViewExtWindows};

pub fn snapshot(view: &WebView, output: PathBuf, complete: impl Fn(Result<()>) + 'static) {
    let complete = Rc::new(complete);
    let callback = complete.clone();
    let result = (|| -> Result<()> {
        // SAFETY: called on the live WebView2 GUI thread; COM owns the callback,
        // which retains its stream until capture has completed.
        unsafe {
            let mut version = windows::core::PWSTR::null();
            view.environment().BrowserVersionString(&mut version)?;
            println!(
                "Selected WebView2 runtime: {}",
                webview2_com::take_pwstr(version)
            );
            let stream = SHCreateStreamOnFileEx(
                &HSTRING::from(output.as_os_str()),
                (STGM_CREATE | STGM_WRITE | STGM_SHARE_EXCLUSIVE).0,
                0,
                true,
                None,
            )?;
            let retained = stream.clone();
            view.controller().CoreWebView2()?.CapturePreview(
                COREWEBVIEW2_CAPTURE_PREVIEW_IMAGE_FORMAT_PNG,
                &stream,
                &CapturePreviewCompletedHandler::create(Box::new(move |status| {
                    drop(retained);
                    callback(status.map_err(Into::into));
                    Ok(())
                })),
            )?;
        }
        Ok(())
    })();
    if let Err(error) = result {
        complete(Err(error));
    }
}
