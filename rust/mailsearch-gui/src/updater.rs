// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Adapt the native Windows updater without introducing an archive write path.
// WinSparkle is loaded only from the executable directory with safe DLL flags.
// Builds require an explicit HTTPS shared feed, Ed25519 key and mapped build.
// Developer binaries lacking those inputs explain why updates are unavailable.
// Manual checks use WinSparkle's confirmation UI, never its immediate installer.
// Cleanup stops updater workers before releasing the DLL on application exit.
use anyhow::{bail, ensure, Context, Result};

pub fn validate_feed(feed: &str) -> Result<()> {
    let url = url::Url::parse(feed).context("Invalid Windows update feed URL")?;
    ensure!(
        url.scheme() == "https" && url.host_str().is_some(),
        "Windows update feed must use HTTPS"
    );
    ensure!(
        url.username().is_empty() && url.password().is_none() && url.fragment().is_none(),
        "Windows update feed must not contain credentials or a fragment"
    );
    Ok(())
}

pub struct Updater {
    pub detail: String,
    #[cfg(target_os = "windows")]
    client: Option<windows::WinSparkle>,
}

impl Updater {
    pub fn new(automatic: bool, quit: impl Fn() + Send + Sync + 'static) -> Self {
        #[cfg(target_os = "windows")]
        {
            match windows::WinSparkle::load(automatic, quit) {
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
        #[cfg(not(target_os = "windows"))]
        {
            let _ = (automatic, quit);
            Self {
                detail:
                    "Automatic updates are not yet connected in this Rust shell on this platform."
                        .into(),
            }
        }
    }

    pub fn available(&self) -> bool {
        #[cfg(target_os = "windows")]
        {
            self.client.is_some()
        }
        #[cfg(not(target_os = "windows"))]
        {
            false
        }
    }

    pub fn configure(&self, automatic: bool) {
        #[cfg(target_os = "windows")]
        if let Some(client) = &self.client {
            client.configure(automatic);
        }
        #[cfg(not(target_os = "windows"))]
        let _ = automatic;
    }

    pub fn check(&self) -> Result<()> {
        #[cfg(target_os = "windows")]
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
        sync::{Mutex, OnceLock},
    };

    type Action = unsafe extern "C" fn();
    type SetAutomatic = unsafe extern "C" fn(c_int);
    type QuitHandler = Box<dyn Fn() + Send + Sync>;
    static QUIT: OnceLock<Mutex<Option<QuitHandler>>> = OnceLock::new();

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
        _library: Library,
        cleanup: Action,
        check: Action,
        automatic: SetAutomatic,
    }

    impl WinSparkle {
        pub fn load(automatic: bool, quit: impl Fn() + Send + Sync + 'static) -> Result<Self> {
            let feed = option_env!("ECT_WINSPARKLE_APPCAST_URL")
                .context("this build has no Windows update feed")?;
            validate_feed(feed)?;
            let key = option_env!("ECT_WINSPARKLE_PUBLIC_KEY")
                .context("this build has no Windows update signing key")?;
            let build = option_env!("ECT_RELEASE_BUILD")
                .context("this build lacks shared release-mapper metadata")?;
            let feed = CString::new(feed)?;
            let key = CString::new(key)?;
            let wide = |value: &str| value.encode_utf16().chain(Some(0)).collect::<Vec<_>>();
            let company = wide("Simson L. Garfinkel");
            let name = wide("Email Collection Toolkit");
            let version = wide(env!("ECT_APP_VERSION"));
            let build = wide(build);
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
                ensure!(
                    set_key(key.as_ptr()) == 1,
                    "WinSparkle rejected the Ed25519 public key"
                );
                details(company.as_ptr(), name.as_ptr(), version.as_ptr());
                set_build(build.as_ptr());
                set_feed(feed.as_ptr());
                set_automatic(i32::from(automatic));
                *QUIT
                    .get_or_init(|| Mutex::new(None))
                    .lock()
                    .map_err(|_| anyhow::anyhow!("Updater callback lock failed"))? =
                    Some(Box::new(quit));
                shutdown(request_quit);
                init();
                Ok(Self {
                    _library: library,
                    cleanup,
                    check,
                    automatic: set_automatic,
                })
            }
        }

        pub fn configure(&self, automatic: bool) {
            // SAFETY: the DLL remains loaded for the lifetime of this client.
            unsafe {
                (self.automatic)(i32::from(automatic));
            }
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
            // SAFETY: stop callbacks/workers while the DLL and handler still live.
            unsafe {
                (self.cleanup)();
            }
            if let Some(lock) = QUIT.get() {
                if let Ok(mut handler) = lock.lock() {
                    *handler = None;
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
                ] {
                    library.get::<Action>(name).unwrap();
                }
                library
                    .get::<SetAutomatic>(b"win_sparkle_set_automatic_check_for_updates\0")
                    .unwrap();
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
