// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Own desktop preferences and update operations outside the archive dispatcher.
// Native menu actions open dialogs in the existing shared webview interface.
// Only the native shell's already-authenticated IPC can reach this state.
// Preferences persist in the per-user configuration directory, never the archive.
// About metadata comes from pyproject.toml through the Cargo build script.
// An unavailable updater stays explicit and does not pretend to perform a check.
use crate::{
    bridge::{Reply, Request},
    preferences::{settings_path, Preferences},
    updater::Updater,
};
use anyhow::{bail, Context, Result};
use serde_json::{json, Value};
use std::path::PathBuf;

pub const SCRIPT: &str = include_str!("../shell.js");

pub struct Shell {
    path: PathBuf,
    preferences: Preferences,
    updater: Updater,
}

impl Shell {
    pub fn new(quit: impl Fn() + Send + Sync + 'static) -> Result<Self> {
        let path = settings_path()?;
        let preferences = Preferences::load(&path)?;
        let updater = Updater::new(preferences.automatic_updates, quit);
        Ok(Self {
            path,
            preferences,
            updater,
        })
    }

    pub fn reply(&mut self, request: Request) -> Reply {
        let result = self.call(&request.method, &request.args);
        match result {
            Ok(value) => Reply {
                id: request.id,
                result: Some(value),
                error: None,
            },
            Err(error) => Reply {
                id: request.id,
                result: None,
                error: Some(format!("{error:#}")),
            },
        }
    }

    fn call(&mut self, method: &str, args: &[Value]) -> Result<Value> {
        match method {
            "shell_status" => Ok(json!({
                "version": env!("ECT_APP_VERSION"),
                "platform": std::env::consts::OS,
                "architecture": std::env::consts::ARCH,
                "preferences": self.preferences,
                "updates_available": self.updater.available(),
                "update_detail": self.updater.detail,
            })),
            "preferences_save" => {
                let preferences: Preferences =
                    serde_json::from_value(args.first().context("Missing preferences")?.clone())?;
                preferences.save(&self.path)?;
                self.updater.configure(preferences.automatic_updates);
                self.preferences = preferences;
                self.call("shell_status", &[])
            }
            "check_updates" => {
                self.updater.check()?;
                Ok(json!(true))
            }
            _ => bail!("Unknown native shell operation"),
        }
    }
}

#[cfg(any(target_os = "macos", target_os = "windows"))]
pub fn menu(window: &tao::window::Window) -> Result<muda::Menu> {
    use muda::{Menu, MenuItem, PredefinedMenuItem, Submenu};
    let about = MenuItem::with_id("about", "About Email Collection Toolkit", true, None);
    let preferences = MenuItem::with_id("preferences", "Preferences…", true, None);
    let updates = MenuItem::with_id("updates", "Check for Updates…", true, None);
    let quit = MenuItem::with_id("quit", "Quit", true, None);
    let separator = PredefinedMenuItem::separator();
    #[cfg(target_os = "macos")]
    let menu = Menu::with_items(&[&Submenu::with_items(
        "Email Collection Toolkit",
        true,
        &[&about, &preferences, &updates, &separator, &quit],
    )?])?;
    #[cfg(target_os = "windows")]
    let menu = Menu::with_items(&[
        &Submenu::with_items("&File", true, &[&quit])?,
        &Submenu::with_items("&Edit", true, &[&preferences])?,
        &Submenu::with_items("&Help", true, &[&updates, &separator, &about])?,
    ])?;
    #[cfg(target_os = "windows")]
    {
        use tao::platform::windows::WindowExtWindows;
        // SAFETY: Tao owns this valid HWND; the menu is kept alive by the event loop.
        unsafe {
            menu.init_for_hwnd(window.hwnd())?;
        }
    }
    #[cfg(target_os = "macos")]
    {
        let _ = window;
        menu.init_for_nsapp();
    }
    Ok(menu)
}
