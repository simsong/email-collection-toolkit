// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Supervise the transitional Python archive engine behind a private JSON pipe.
// Reading and searching stay in Rust; explicit processing operations use this peer.
// Short responses have deadlines; recovery waits for completion or explicit abort.
// EOF cancels imports at message boundaries, followed by a five-second exit bound.
// Unix process groups and Windows job objects contain ordinary helper descendants.
// No shell interprets archive paths, executable names, or request arguments.
use anyhow::{bail, ensure, Context, Result};
use serde_json::{json, Value};
use std::{
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver, RecvTimeoutError},
    },
    time::{Duration, Instant},
};

pub const ARCHIVE_WRITING_SUPPORTED: bool = !cfg!(windows);
pub const WRITE_UNAVAILABLE: &str =
    "Windows archive writing is not supported. Open an existing archive for reading.";
pub fn require_archive_writing() -> Result<()> {
    ensure!(ARCHIVE_WRITING_SUPPORTED, WRITE_UNAVAILABLE);
    Ok(())
}

pub struct Engine {
    child: Child,
    input: Option<ChildStdin>,
    replies: Receiver<String>,
    next_id: u64,
    failed: bool,
    #[cfg(target_os = "windows")]
    _job: Job,
}
impl Engine {
    pub fn open(archive: &Path) -> Result<Self> {
        Self::spawn(archive, None)
    }
    pub fn create_archive(archive: &Path, abort: &AtomicBool) -> Result<()> {
        require_archive_writing()?;
        let mut engine = Self::spawn(archive, Some(abort))?;
        engine.request("create", &[], Some(abort))?;
        ensure!(!abort.load(Ordering::Acquire), "Archive creation aborted");
        Ok(())
    }
    pub fn for_recovery(archive: &Path, abort: &AtomicBool) -> Result<Self> {
        Self::spawn(archive, Some(abort))
    }
    fn spawn(archive: &Path, abort: Option<&AtomicBool>) -> Result<Self> {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
        let executable = std::env::current_exe()?;
        let directory = executable
            .parent()
            .context("Missing executable directory")?;
        let frozen = cfg!(target_os = "macos")
            && directory
                .parent()
                .is_some_and(|contents| contents.join("Info.plist").is_file());
        let service = directory.join("archive-service");
        let packaged = directory.join("python/python.exe");
        let python = std::env::var_os("ECT_RUST_ENGINE_PYTHON")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                if cfg!(windows) && packaged.is_file() {
                    return packaged;
                }
                root.join(if cfg!(windows) {
                    ".venv/Scripts/python.exe"
                } else {
                    ".venv/bin/python"
                })
            });
        // A macOS bundle never falls back to the build machine's checkout.
        let mut command = if frozen {
            ensure!(service.is_file(), "Bundled archive service is missing");
            let mut command = Command::new(service);
            command.arg("--rust-engine");
            command
        } else {
            ensure!(python.is_file(), "Archive engine is unavailable. Run uv sync --locked, or set ECT_RUST_ENGINE_PYTHON to the project Python executable.");
            let mut command = Command::new(python);
            command.args(["-m", "mailarchiver.rust_engine"]);
            command
        };
        command
            .arg(archive)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000); // CREATE_NO_WINDOW
        }
        let mut child = command.spawn().context("Start archive engine")?;
        #[cfg(target_os = "windows")]
        let job = match Job::assign(&child) {
            Ok(job) => job,
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(error);
            }
        };
        let input = child.stdin.take();
        let output = child.stdout.take().context("Missing engine output")?;
        let (send, replies) = mpsc::sync_channel(64);
        std::thread::spawn(move || {
            for line in BufReader::new(output).lines() {
                match line {
                    Ok(line) => {
                        if send.send(line).is_err() {
                            break;
                        }
                    }
                    Err(_) => break,
                }
            }
        });
        let mut engine = Self {
            child,
            input,
            replies,
            next_id: 0,
            failed: false,
            #[cfg(target_os = "windows")]
            _job: job,
        };
        engine.request("ping", &[], abort)?;
        Ok(engine)
    }
    pub fn call(&mut self, method: &str, args: &[Value]) -> Result<Value> {
        ensure!(
            method != "recover",
            "Recovery requires a supervised opening job"
        );
        self.request(method, args, None)
    }
    pub fn recover(&mut self, abort: &AtomicBool) -> Result<Value> {
        self.request("recover", &[], Some(abort))
    }
    fn request(
        &mut self,
        method: &str,
        args: &[Value],
        abort: Option<&AtomicBool>,
    ) -> Result<Value> {
        if let Some(abort) = abort {
            ensure!(
                !abort.load(Ordering::Acquire),
                "Opening aborted. The archive cannot be opened."
            );
        }
        ensure!(!self.failed,"Archive engine connection failed; close and reopen this window before retrying archive operations");
        self.failed = true;
        self.next_id += 1;
        let input = self.input.as_mut().context("Engine is shutting down")?;
        writeln!(
            input,
            "{}",
            json!({"id":self.next_id,"method":method,"args":args})
        )?;
        input.flush()?;
        let line = if let Some(abort) = abort {
            loop {
                ensure!(
                    !abort.load(Ordering::Acquire),
                    "Opening aborted. The archive cannot be opened."
                );
                match self.replies.recv_timeout(Duration::from_millis(50)) {
                    Ok(line) => break line,
                    Err(RecvTimeoutError::Timeout) => (),
                    Err(error) => return Err(error).context("Archive opening engine disconnected"),
                }
            }
        } else {
            self.replies
                .recv_timeout(Duration::from_secs(30))
                .context("Archive engine did not respond within 30 seconds")?
        };
        let reply: crate::bridge::Reply = serde_json::from_str(&line)?;
        ensure!(
            reply.id == self.next_id,
            "Engine response is out of sequence; reopen this window"
        );
        self.failed = false;
        if let Some(error) = reply.error {
            bail!("{error}");
        }
        Ok(reply.result.unwrap_or(Value::Null))
    }
}
impl Drop for Engine {
    fn drop(&mut self) {
        self.input.take();
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if !matches!(self.child.try_wait(), Ok(None)) {
                return;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        #[cfg(unix)]
        unsafe {
            libc::kill(-(self.child.id() as i32), libc::SIGKILL);
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
#[cfg(target_os = "windows")]
struct Job(windows_sys::Win32::Foundation::HANDLE);
#[cfg(target_os = "windows")]
unsafe impl Send for Job {}
#[cfg(target_os = "windows")]
impl Job {
    fn assign(child: &Child) -> Result<Self> {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::System::JobObjects::*;
        // SAFETY: handles are validated; initialized structures have exact Win32 sizes.
        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            ensure!(
                !handle.is_null(),
                "Create engine job: {}",
                std::io::Error::last_os_error()
            );
            let job = Self(handle);
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            ensure!(
                SetInformationJobObject(
                    handle,
                    JobObjectExtendedLimitInformation,
                    &limits as *const _ as *const _,
                    std::mem::size_of_val(&limits) as u32
                ) != 0,
                "Configure engine job: {}",
                std::io::Error::last_os_error()
            );
            ensure!(
                AssignProcessToJobObject(handle, child.as_raw_handle() as _) != 0,
                "Assign engine job: {}",
                std::io::Error::last_os_error()
            );
            Ok(job)
        }
    }
}
#[cfg(target_os = "windows")]
impl Drop for Job {
    fn drop(&mut self) {
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(self.0);
        }
    }
}
