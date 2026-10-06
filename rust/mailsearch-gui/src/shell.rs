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
            "shell_status" => {
                self.preferences = Preferences::load(&self.path)?;
                self.updater.configure(self.preferences.automatic_updates);
                Ok(json!({
                "version": env!("ECT_APP_VERSION"),
                "platform": std::env::consts::OS,
                "architecture": std::env::consts::ARCH,
                "preferences": self.preferences,
                "updates_available": self.updater.available(),
                "update_detail": self.updater.detail,
                }))
            }
            "preferences_save" => {
                let preferences: Preferences =
                    serde_json::from_value(args.first().context("Missing preferences")?.clone())?;
                let baseline: Preferences = serde_json::from_value(
                    args.get(1)
                        .context("Missing displayed preference baseline")?
                        .clone(),
                )?;
                self.preferences = preferences.merge_save(&self.path, &baseline)?;
                self.updater.configure(self.preferences.automatic_updates);
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
pub struct NativeMenu {
    _menu: muda::Menu,
    recent: crate::documents::Documents,
    writes: [muda::MenuItem; 3],
    history: muda::MenuItem,
}
#[cfg(any(target_os = "macos", target_os = "windows"))]
impl NativeMenu {
    pub fn recent_path(&self, index: usize) -> Option<&std::path::Path> {
        self.recent.recent_path(index)
    }
    pub fn capabilities(&self, status: &Value) {
        let available = status["available"] == true;
        let writes = available
            && status["write_available"] == true
            && crate::engine::ARCHIVE_WRITING_SUPPORTED;
        for item in &self.writes {
            item.set_enabled(writes);
        }
        self.history.set_enabled(available);
    }
    #[cfg(feature = "native-smoke")]
    pub fn state(&self) -> Value {
        json!({"writes":self.writes.iter().all(muda::MenuItem::is_enabled),"history":self.history.is_enabled()})
    }
}
#[cfg(any(target_os = "macos", target_os = "windows"))]
pub fn menu(window: &tao::window::Window) -> Result<NativeMenu> {
    use muda::accelerator::{Accelerator, Code, Modifiers};
    use muda::{Menu, MenuItem, PredefinedMenuItem, Submenu};
    let shortcut = |code| {
        Some(Accelerator::new(
            if cfg!(target_os = "macos") {
                Modifiers::META
            } else {
                Modifiers::CONTROL
            },
            code,
        ))
    };
    let about = MenuItem::with_id("about", "About Email Collection Toolkit", true, None);
    let preferences = MenuItem::with_id("preferences", "Preferences…", true, shortcut(Code::Comma));
    let updates = MenuItem::with_id("updates", "Check for Updates…", true, None);
    let quit = MenuItem::with_id("quit", "Quit", true, shortcut(Code::KeyQ));
    let separator = PredefinedMenuItem::separator();
    let open = MenuItem::with_id("open_archive", "Open Archive…", true, shortcut(Code::KeyO));
    let new_search = MenuItem::with_id("new_search_window", "New Search Window", true, None);
    let recent = Submenu::new("Open Recent", true);
    let documents = crate::documents::Documents::load(&crate::documents::Documents::path()?)?;
    for (index, path) in documents.recent.iter().enumerate() {
        recent.append(&MenuItem::with_id(
            format!("recent-{index}"),
            path.to_string_lossy(),
            true,
            None,
        ))?;
    }
    let writes = false; // Enable only after the archive helper reports capabilities.
    let new = MenuItem::with_id("new_archive", "New Archive…", writes, None);
    let import = MenuItem::with_id("import_directory", "Import…", writes, None);
    let options = MenuItem::with_id("open_options", "Owner Emails…", writes, None);
    let history = MenuItem::with_id("open_ingest_window", "Import History", false, None);
    let file_menu = Submenu::with_items(
        "File",
        true,
        &[
            &new,
            &open,
            &recent,
            &new_search,
            &import,
            &options,
            &history,
        ],
    )?;
    #[cfg(target_os = "macos")]
    let edit_menu = Submenu::with_items(
        "Edit",
        true,
        &[
            &PredefinedMenuItem::undo(None),
            &PredefinedMenuItem::redo(None),
            &PredefinedMenuItem::separator(),
            &PredefinedMenuItem::cut(None),
            &PredefinedMenuItem::copy(None),
            &PredefinedMenuItem::paste(None),
            &PredefinedMenuItem::select_all(None),
        ],
    )?;
    #[cfg(target_os = "macos")]
    let menu = Menu::with_items(&[
        &Submenu::with_items(
            "Email Collection Toolkit",
            true,
            &[&about, &preferences, &updates, &separator, &quit],
        )?,
        &file_menu,
        &edit_menu,
    ])?;
    #[cfg(target_os = "windows")]
    let menu = Menu::with_items(&[
        &file_menu,
        &Submenu::with_items("&Edit", true, &[&preferences])?,
        &Submenu::with_items("&Help", true, &[&updates, &separator, &about, &quit])?,
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
    Ok(NativeMenu {
        _menu: menu,
        recent: documents,
        writes: [new, import, options],
        history,
    })
}
