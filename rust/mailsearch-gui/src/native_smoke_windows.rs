// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Capture the real WebView2 surface after native smoke assertions finish.
// The COM stream writes only to the explicit synthetic test output path.
// Keep the stream alive until WebView2 reports capture completion.
// Completion returns through Tao so capture errors fail the native process.
// This implementation is excluded from ordinary application builds.
// macOS retains its existing independent WKWebView capture implementation.
// Accelerator trials post to the owned window and restore thread-local key state.
use anyhow::{ensure, Result};
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
use windows_sys::Win32::UI::{
    Input::KeyboardAndMouse::{
        GetKeyboardState, SetKeyboardState, VK_CONTROL, VK_LCONTROL, VK_LMENU, VK_LSHIFT, VK_LWIN,
        VK_MENU, VK_OEM_COMMA, VK_RCONTROL, VK_RMENU, VK_RSHIFT, VK_RWIN, VK_SHIFT,
    },
    WindowsAndMessaging::{PostMessageW, WM_KEYDOWN},
};
use wry::{WebView, WebViewExtWindows};

pub struct KeyboardState([u8; 256]);

impl KeyboardState {
    pub fn preferences(window: &tao::window::Window) -> Result<Self> {
        use tao::platform::windows::WindowExtWindows;
        let mut original = [0; 256];
        // SAFETY: runs on the owning GUI thread. These APIs affect only its
        // keyboard table; the posted message targets its live HWND, not global input.
        unsafe {
            ensure!(
                GetKeyboardState(original.as_mut_ptr()) != 0,
                "Read keyboard state failed"
            );
            let mut keys = original;
            for key in [
                VK_CONTROL,
                VK_LCONTROL,
                VK_RCONTROL,
                VK_SHIFT,
                VK_LSHIFT,
                VK_RSHIFT,
                VK_MENU,
                VK_LMENU,
                VK_RMENU,
                VK_LWIN,
                VK_RWIN,
            ] {
                keys[key as usize] = 0;
            }
            keys[VK_CONTROL as usize] = 0x80;
            keys[VK_LCONTROL as usize] = 0x80;
            ensure!(
                SetKeyboardState(keys.as_ptr()) != 0,
                "Set keyboard state failed"
            );
            let guard = Self(original);
            ensure!(
                PostMessageW(window.hwnd() as _, WM_KEYDOWN, VK_OEM_COMMA as usize, 1) != 0,
                "Queue Preferences accelerator failed"
            );
            Ok(guard)
        }
    }
}

impl Drop for KeyboardState {
    fn drop(&mut self) {
        // SAFETY: guard stays on the same GUI thread and retains the original table.
        if unsafe { SetKeyboardState(self.0.as_ptr()) } == 0 {
            eprintln!("Restore native smoke keyboard state failed");
        }
    }
}

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
