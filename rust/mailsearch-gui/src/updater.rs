// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Present one Rust updater interface over native Sparkle and WinSparkle adapters.
// WinSparkle is loaded only from the executable directory with safe DLL flags.
// Builds require an explicit HTTPS shared feed, Ed25519 key and mapped build.
// Developer binaries lacking those inputs explain why updates are unavailable.
// Manual checks use WinSparkle's confirmation UI, never its immediate installer.
// The Windows DLL stays pinned through exit because cleanup does not join workers.
use crate::update_policy::Channel;
use anyhow::{bail, ensure, Context, Result};
#[cfg(target_os = "macos")]
#[path = "updater_macos.rs"]
mod macos;

pub fn validate_feed(feed: &str) -> Result<()> {
    let url = url::Url::parse(feed).context("Invalid update feed URL")?;
    ensure!(
        url.scheme() == "https" && url.host_str().is_some(),
        "Update feed must use HTTPS"
    );
    ensure!(
        url.username().is_empty() && url.password().is_none() && url.fragment().is_none(),
        "Update feed must not contain credentials or a fragment"
    );
    Ok(())
}

pub struct Updater {
    pub detail: String,
    #[cfg(target_os = "windows")]
    client: Option<windows::WinSparkle>,
    #[cfg(target_os = "macos")]
    client: Option<macos::MacSparkle>,
}

impl Updater {
    pub fn new(automatic: bool, channel: Channel, quit: impl Fn() + Send + Sync + 'static) -> Self {
        #[cfg(target_os = "windows")]
        {
            match windows::WinSparkle::load(automatic, channel, quit) {
                Ok(client) => Self {
                    detail: "Updates use WinSparkle and require confirmation before installation."
                        .into(),
                    client: Some(client),
                },
                Err(error) => Self {
                    detail: format!("Windows updates unavailable: {error:#}"),
                    client: None,
                },
            }
        }
        #[cfg(target_os = "macos")]
        {
            let _ = quit;
            match crate::update_policy::Configuration::embedded().and_then(|configuration| {
                macos::MacSparkle::load(&configuration, automatic, channel)
            }) {
                Ok(client) => Self {
                    detail:
                        "Updates use Sparkle; downloading and installation require confirmation."
                            .into(),
                    client: Some(client),
                },
                Err(error) => Self {
                    detail: format!("macOS updates unavailable: {error:#}"),
                    client: None,
                },
            }
        }
        #[cfg(not(any(target_os = "windows", target_os = "macos")))]
        {
            let _ = (automatic, channel, quit);
            Self {
                detail:
                    "Automatic updates are not yet connected in this Rust shell on this platform."
                        .into(),
            }
        }
    }

    pub fn available(&self) -> bool {
        #[cfg(any(target_os = "windows", target_os = "macos"))]
        {
            self.client.is_some()
        }
        #[cfg(not(any(target_os = "windows", target_os = "macos")))]
        {
            false
        }
    }

    pub fn configure(&self, automatic: bool, channel: Channel) {
        #[cfg(any(target_os = "windows", target_os = "macos"))]
        if let Some(client) = &self.client {
            client.configure(automatic, channel);
        }
        #[cfg(not(any(target_os = "windows", target_os = "macos")))]
        let _ = (automatic, channel);
    }

    pub fn check(&self) -> Result<()> {
        #[cfg(any(target_os = "windows", target_os = "macos"))]
        if let Some(client) = &self.client {
            client.check();
            return Ok(());
        }
        bail!("{}", self.detail)
    }
}

#[cfg(target_os = "windows")]
mod windows {
    use super::*;
    use libloading::os::windows::{
        Library, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR, LOAD_LIBRARY_SEARCH_SYSTEM32,
    };
    use std::{
        ffi::{c_char, c_int, CString},
        mem::ManuallyDrop,
        sync::{
            atomic::{AtomicBool, Ordering},
            Arc, Mutex, OnceLock,
        },
        time::{Duration, SystemTime, UNIX_EPOCH},
    };

    type Action = unsafe extern "C" fn();
    type SetAutomatic = unsafe extern "C" fn(c_int);
    type QuitHandler = Box<dyn Fn() + Send + Sync>;
    static QUIT: OnceLock<Mutex<Option<QuitHandler>>> = OnceLock::new();
    static INSTALLATION: OnceLock<Mutex<Option<Arc<crate::update_policy::Installation>>>> =
        OnceLock::new();

    unsafe extern "C" fn can_shutdown() -> c_int {
        INSTALLATION
            .get()
            .and_then(|state| state.lock().ok())
            .and_then(|state| {
                state
                    .as_ref()
                    .map(|installation| match installation.reserve() {
                        Ok(true) => {
                            installation.installing();
                            1
                        }
                        _ => 0,
                    })
            })
            .unwrap_or(0)
    }

    unsafe extern "C" fn cancel() {
        if let Some(state) = INSTALLATION.get() {
            if let Ok(state) = state.lock() {
                if let Some(installation) = state.as_ref() {
                    installation.cancel();
                }
            }
        }
    }

    unsafe extern "C" fn request_quit() {
        // WinSparkle calls from its own thread; only enqueue a native event.
        if let Some(lock) = QUIT.get() {
            if let Ok(handler) = lock.lock() {
                if let Some(handler) = handler.as_ref() {
                    handler();
                }
            }
        }
    }

    pub struct WinSparkle {
        _library: ManuallyDrop<Library>,
        cleanup: Action,
        check: Action,
        automatic: Arc<AtomicBool>,
        stop: Arc<AtomicBool>,
        gateway: crate::updater_gateway::Gateway,
    }

    impl WinSparkle {
        pub fn load(
            automatic: bool,
            channel: Channel,
            quit: impl Fn() + Send + Sync + 'static,
        ) -> Result<Self> {
            let configuration = crate::update_policy::Configuration::embedded()?;
            let gateway = crate::updater_gateway::Gateway::start(
                configuration.feed,
                configuration.key,
                channel,
            )?;
            let feed = CString::new(gateway.url.as_str())?;
            let key = CString::new(configuration.key)?;
            let wide = |value: &str| value.encode_utf16().chain(Some(0)).collect::<Vec<_>>();
            let company = wide("Simson L. Garfinkel");
            let name = wide("Email Collection Toolkit");
            let version = wide(env!("ECT_APP_VERSION"));
            let build = wide(configuration.build);
            let dll = std::env::current_exe()?
                .parent()
                .context("Executable directory is unavailable")?
                .join("WinSparkle.dll");
            // SAFETY: the absolute sibling DLL is an explicit packaging input;
            // dependency lookup excludes the current directory and PATH. Each
            // symbol uses the C ABI declared by WinSparkle 0.9's official header.
            unsafe {
                let library = Library::load_with_flags(
                    &dll,
                    LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32,
                )
                .with_context(|| format!("load {}", dll.display()))?;
                let init = *library.get::<Action>(b"win_sparkle_init\0")?;
                let cleanup = *library.get::<Action>(b"win_sparkle_cleanup\0")?;
                let check = *library.get::<Action>(b"win_sparkle_check_update_with_ui\0")?;
                let set_automatic = *library
                    .get::<SetAutomatic>(b"win_sparkle_set_automatic_check_for_updates\0")?;
                let details = *library
                    .get::<unsafe extern "C" fn(*const u16, *const u16, *const u16)>(
                        b"win_sparkle_set_app_details\0",
                    )?;
                let set_build = *library.get::<unsafe extern "C" fn(*const u16)>(
                    b"win_sparkle_set_app_build_version\0",
                )?;
                let set_feed = *library
                    .get::<unsafe extern "C" fn(*const c_char)>(b"win_sparkle_set_appcast_url\0")?;
                let set_key = *library.get::<unsafe extern "C" fn(*const c_char) -> c_int>(
                    b"win_sparkle_set_eddsa_public_key\0",
                )?;
                let shutdown = *library.get::<unsafe extern "C" fn(unsafe extern "C" fn())>(
                    b"win_sparkle_set_shutdown_request_callback\0",
                )?;
                let ready = *library.get::<unsafe extern "C" fn(unsafe extern "C" fn() -> c_int)>(
                    b"win_sparkle_set_can_shutdown_callback\0",
                )?;
                let canceled = *library.get::<unsafe extern "C" fn(unsafe extern "C" fn())>(
                    b"win_sparkle_set_update_cancelled_callback\0",
                )?;
                let errors = *library.get::<unsafe extern "C" fn(unsafe extern "C" fn())>(
                    b"win_sparkle_set_error_callback\0",
                )?;
                let interval = *library.get::<unsafe extern "C" fn(c_int)>(
                    b"win_sparkle_set_update_check_interval\0",
                )?;
                let background =
                    *library.get::<Action>(b"win_sparkle_check_update_without_ui\0")?;
                let last_check = *library
                    .get::<unsafe extern "C" fn() -> i64>(b"win_sparkle_get_last_check_time\0")?;
                ensure!(
                    set_key(key.as_ptr()) == 1,
                    "WinSparkle rejected the Ed25519 public key"
                );
                details(company.as_ptr(), name.as_ptr(), version.as_ptr());
                set_build(build.as_ptr());
                set_feed(feed.as_ptr());
                // SDK preference writes do not start its periodic worker at
                // runtime. Own scheduling so enabling checks takes effect now.
                set_automatic(0);
                interval(crate::update_policy::DAILY_SECONDS as c_int);
                *INSTALLATION
                    .get_or_init(|| Mutex::new(None))
                    .lock()
                    .map_err(|_| anyhow::anyhow!("Updater reservation lock failed"))? =
                    Some(Arc::new(crate::update_policy::Installation::default()));
                *QUIT
                    .get_or_init(|| Mutex::new(None))
                    .lock()
                    .map_err(|_| anyhow::anyhow!("Updater callback lock failed"))? =
                    Some(Box::new(quit));
                shutdown(request_quit);
                ready(can_shutdown);
                canceled(cancel);
                errors(cancel);
                init();
                let automatic = Arc::new(AtomicBool::new(automatic));
                let stop = Arc::new(AtomicBool::new(false));
                let enabled = automatic.clone();
                let stopped = stop.clone();
                // Pin before dispatch: SDK workers can outlive its UI cleanup.
                let library = ManuallyDrop::new(library);
                std::thread::Builder::new()
                    .name("native-update-schedule".into())
                    .spawn(move || {
                        let mut next = std::time::Instant::now();
                        while !stopped.load(Ordering::Acquire) {
                            if enabled.load(Ordering::Acquire) && std::time::Instant::now() >= next
                            {
                                let now = SystemTime::now()
                                    .duration_since(UNIX_EPOCH)
                                    .unwrap_or_default()
                                    .as_secs();
                                let wait =
                                    crate::update_policy::seconds_until_check(last_check(), now);
                                if wait == 0 {
                                    background();
                                }
                                next = std::time::Instant::now()
                                    + Duration::from_secs(if wait == 0 {
                                        crate::update_policy::DAILY_SECONDS
                                    } else {
                                        wait
                                    });
                            }
                            std::thread::sleep(Duration::from_secs(1));
                        }
                    })
                    .context("Start native update schedule")?;
                Ok(Self {
                    _library: library,
                    cleanup,
                    check,
                    automatic,
                    stop,
                    gateway,
                })
            }
        }

        pub fn configure(&self, automatic: bool, channel: Channel) {
            self.gateway.configure(channel);
            self.automatic.store(automatic, Ordering::Release);
        }

        pub fn check(&self) {
            // SAFETY: initialized WinSparkle owns its confirmation UI and threads.
            unsafe {
                (self.check)();
            }
        }
    }

    impl Drop for WinSparkle {
        fn drop(&mut self) {
            self.stop.store(true, Ordering::Release);
            // SAFETY: stop UI callbacks while the permanently pinned DLL lives.
            unsafe {
                (self.cleanup)();
            }
            if let Some(lock) = QUIT.get() {
                if let Ok(mut handler) = lock.lock() {
                    *handler = None;
                }
            }
            if let Some(lock) = INSTALLATION.get() {
                if let Ok(mut installation) = lock.lock() {
                    installation.take();
                }
            }
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        #[ignore = "requires the checksum-verified native WinSparkle SDK; run cargo reader-updater-check"]
        fn winsparkle_sdk_accepts_only_valid_public_keys() {
            // Update-client requirement: validate the real SDK ABI and key parser
            // without network checks, registry preferences, or installing an update.
            let path = std::env::var_os("ECT_WINSPARKLE_TEST_DLL")
                .expect("set ECT_WINSPARKLE_TEST_DLL to the staged native DLL");
            let path = std::path::PathBuf::from(path).canonicalize().unwrap();
            // SAFETY: this opt-in test uses the explicitly staged, verified SDK;
            // every signature is the same official C ABI used by the client.
            unsafe {
                let library = Library::load_with_flags(
                    path,
                    LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32,
                )
                .unwrap();
                for name in [
                    b"win_sparkle_init\0".as_slice(),
                    b"win_sparkle_cleanup\0",
                    b"win_sparkle_check_update_with_ui\0",
                    b"win_sparkle_check_update_without_ui\0",
                ] {
                    library.get::<Action>(name).unwrap();
                }
                library
                    .get::<SetAutomatic>(b"win_sparkle_set_automatic_check_for_updates\0")
                    .unwrap();
                library
                    .get::<unsafe extern "C" fn(c_int)>(b"win_sparkle_set_update_check_interval\0")
                    .unwrap();
                library
                    .get::<unsafe extern "C" fn() -> i64>(b"win_sparkle_get_last_check_time\0")
                    .unwrap();
                library
                    .get::<unsafe extern "C" fn(unsafe extern "C" fn() -> c_int)>(
                        b"win_sparkle_set_can_shutdown_callback\0",
                    )
                    .unwrap();
                for name in [
                    b"win_sparkle_set_update_cancelled_callback\0".as_slice(),
                    b"win_sparkle_set_error_callback\0",
                ] {
                    library
                        .get::<unsafe extern "C" fn(unsafe extern "C" fn())>(name)
                        .unwrap();
                }
                library
                    .get::<unsafe extern "C" fn(*const u16, *const u16, *const u16)>(
                        b"win_sparkle_set_app_details\0",
                    )
                    .unwrap();
                library
                    .get::<unsafe extern "C" fn(*const u16)>(b"win_sparkle_set_app_build_version\0")
                    .unwrap();
                library
                    .get::<unsafe extern "C" fn(*const c_char)>(b"win_sparkle_set_appcast_url\0")
                    .unwrap();
                library
                    .get::<unsafe extern "C" fn(unsafe extern "C" fn())>(
                        b"win_sparkle_set_shutdown_request_callback\0",
                    )
                    .unwrap();
                let set_key = library
                    .get::<unsafe extern "C" fn(*const c_char) -> c_int>(
                        b"win_sparkle_set_eddsa_public_key\0",
                    )
                    .unwrap();
                let metadata = include_str!("../../../src/mailarchiver/update_metadata.py");
                let public_key = metadata
                    .lines()
                    .find_map(|line| line.strip_prefix("SPARKLE_PUBLIC_KEY = \""))
                    .unwrap()
                    .trim_end_matches('"');
                assert_eq!(set_key(CString::new(public_key).unwrap().as_ptr()), 1);
                assert_eq!(set_key(c"invalid key".as_ptr()), 0);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn windows_updates_allow_the_shared_https_feed() {
        // Update requirements: both clients may use one HTTPS appcast URL.
        // Platform selection belongs to explicit sparkle:os enclosure metadata.
        assert!(validate_feed("https://example.test/windows/arm64/preview.xml").is_ok());
        assert!(validate_feed(
            "https://simsong.github.io/email-collection-toolkit/updates/mac/appcast.xml"
        )
        .is_ok());
        for feed in [
            "http://example.test/feed.xml",
            "file:///feed.xml",
            "https://user:secret@example.test/feed.xml",
            "https://example.test/feed.xml#x",
        ] {
            assert!(validate_feed(feed).is_err(), "{feed}");
        }
    }
}
