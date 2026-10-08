// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Share release configuration and installation exclusion across native updaters.
// Packaging obtains version, build, feed and public key from the existing mapper.
// Source launches without that metadata cannot start update network checks.
// macOS installation takes the same exclusive OS lock as Python's writer fence.
// Retain that lock until cancellation or process exit, preventing new writers.
// Windows currently has no archive writers; its adapter still shares this policy.
use anyhow::{ensure, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::{Deserialize, Serialize};
use std::{
    fs::File,
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
};

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Channel {
    Release,
    Preview,
}
impl Default for Channel {
    fn default() -> Self {
        if env!("ECT_APP_VERSION").contains(['a', 'b']) {
            Self::Preview
        } else {
            Self::Release
        }
    }
}

pub struct Configuration {
    pub feed: &'static str,
    pub key: &'static str,
    pub build: &'static str,
}

pub const DAILY_SECONDS: u64 = 24 * 60 * 60;

pub fn seconds_until_check(last: i64, now: u64) -> u64 {
    if last < 0 {
        return 0;
    }
    (last as u64)
        .saturating_add(DAILY_SECONDS)
        .saturating_sub(now)
        .min(DAILY_SECONDS)
}

#[cfg(test)]
mod tests {
    #[test]
    fn daily_schedule_handles_new_installs_manual_checks_and_clock_changes() {
        assert_eq!(super::seconds_until_check(-1, 100), 0);
        assert_eq!(
            super::seconds_until_check(100, 101),
            super::DAILY_SECONDS - 1
        );
        assert_eq!(
            super::seconds_until_check(100, 100 + super::DAILY_SECONDS),
            0
        );
        assert_eq!(super::seconds_until_check(100, 50), super::DAILY_SECONDS);
    }
}

impl Configuration {
    pub fn embedded() -> Result<Self> {
        let feed = option_env!("ECT_UPDATE_FEED_URL").context("No packaged update feed")?;
        crate::updater::validate_feed(feed)?;
        let key = option_env!("ECT_UPDATE_PUBLIC_KEY").context("No packaged signing key")?;
        ensure!(
            STANDARD.decode(key)?.len() == 32,
            "Invalid Ed25519 public key"
        );
        let build = option_env!("ECT_RELEASE_BUILD").context("No mapped release build")?;
        ensure!(build.parse::<u64>()? > 0, "Invalid release build");
        Ok(Self { feed, key, build })
    }
}

#[derive(Default)]
pub struct Installation {
    guard: Mutex<Option<File>>,
    installing: AtomicBool,
}

impl Installation {
    pub fn reserve(&self) -> Result<bool> {
        let guard = self
            .guard
            .lock()
            .map_err(|_| anyhow::anyhow!("Update reservation lock failed"))?;
        if guard.is_some() {
            return Ok(true);
        }
        #[cfg(unix)]
        {
            let mut guard = guard;
            match writer_guard() {
                Ok(file) => *guard = Some(file),
                Err(error)
                    if error.downcast_ref::<std::io::Error>().is_some_and(|error| {
                        matches!(error.raw_os_error(), Some(libc::EAGAIN | libc::EACCES))
                    }) =>
                {
                    return Ok(false)
                }
                Err(error) => return Err(error),
            }
        }
        Ok(true)
    }

    pub fn cancel(&self) {
        self.installing.store(false, Ordering::Release);
        if let Ok(mut guard) = self.guard.lock() {
            guard.take();
        }
    }

    pub fn is_installing(&self) -> bool {
        self.installing.load(Ordering::Acquire)
    }

    pub fn installing(&self) {
        self.installing.store(true, Ordering::Release);
    }

    #[cfg(target_os = "macos")]
    pub fn invoke(&self, continuation: &block2::Block<dyn Fn()>) -> Result<()> {
        self.installing();
        if let Err(error) =
            objc2::exception::catch(std::panic::AssertUnwindSafe(|| continuation.call(())))
        {
            self.cancel();
            anyhow::bail!("Native installation continuation failed: {error:?}");
        }
        Ok(())
    }
}

impl Drop for Installation {
    fn drop(&mut self) {
        if self.installing.load(Ordering::Acquire) {
            // Keep the OS fence through native relaunch; kernel process exit
            // releases it. A failed/canceled cycle calls cancel instead.
            if let Ok(guard) = self.guard.get_mut() {
                if let Some(file) = guard.take() {
                    std::mem::forget(file);
                }
            }
        }
    }
}

#[cfg(unix)]
fn writer_guard() -> Result<File> {
    use std::{
        ffi::CString,
        os::unix::{
            fs::{DirBuilderExt, MetadataExt, OpenOptionsExt},
            io::{AsRawFd, FromRawFd},
        },
    };
    // SAFETY: getuid has no pointer arguments or ownership requirements.
    let uid = unsafe { libc::getuid() };
    let directory = std::env::temp_dir().join(format!("mailarchiver-writers-{uid}"));
    match std::fs::DirBuilder::new().mode(0o700).create(&directory) {
        Ok(()) => (),
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => (),
        Err(error) => return Err(error.into()),
    }
    let directory = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW)
        .open(directory)?;
    let metadata = directory.metadata()?;
    ensure!(
        metadata.uid() == uid && metadata.mode() & 0o077 == 0,
        "Writer guard directory must be private and owned by this user"
    );
    let name = CString::new("application-write.lock")?;
    // SAFETY: the live directory descriptor pins the validated directory; the
    // NUL-terminated name has no path separators. The returned FD is owned here.
    let fd = unsafe {
        libc::openat(
            directory.as_raw_fd(),
            name.as_ptr(),
            libc::O_RDWR | libc::O_CREAT | libc::O_NOFOLLOW | libc::O_NONBLOCK | libc::O_CLOEXEC,
            0o600,
        )
    };
    ensure!(
        fd >= 0,
        "Could not open update writer fence: {}",
        std::io::Error::last_os_error()
    );
    let file = unsafe { File::from_raw_fd(fd) };
    let metadata = file.metadata()?;
    ensure!(
        metadata.is_file() && metadata.nlink() == 1 && metadata.uid() == uid,
        "Writer fence must be a regular owned file with one link"
    );
    // SAFETY: flock operates on the owned live file; Drop releases this lock.
    if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    Ok(file)
}
