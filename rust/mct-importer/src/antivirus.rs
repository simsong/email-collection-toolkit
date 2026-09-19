// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

//! Scan before emission; infected records alone receive antivirus headers.
use anyhow::{ensure, Context, Result};
use libloading::Library;
use std::{
    ffi::{c_char, c_int, c_uint, c_ulong, c_void, CStr, CString},
    fs::File,
    io::{BufRead, BufReader, Read, Seek, Write},
    path::{Path, PathBuf},
};

#[repr(C)]
struct ScanOptions {
    general: u32,
    parse: u32,
    heuristic: u32,
    mail: u32,
    dev: u32,
}
type Init = unsafe extern "C" fn(c_uint) -> c_int;
type New = unsafe extern "C" fn() -> *mut c_void;
type Free = unsafe extern "C" fn(*mut c_void) -> c_int;
type Load = unsafe extern "C" fn(*const c_char, *mut c_void, *mut c_uint, c_uint) -> c_int;
type Compile = unsafe extern "C" fn(*mut c_void) -> c_int;
type SetStr = unsafe extern "C" fn(*mut c_void, c_int, *const c_char) -> c_int;
type Version = unsafe extern "C" fn() -> *const c_char;
type ErrorText = unsafe extern "C" fn(c_int) -> *const c_char;
type Scan = unsafe extern "C" fn(
    *const c_char,
    *mut *const c_char,
    *mut c_ulong,
    *mut c_void,
    *mut ScanOptions,
) -> c_int;

pub struct Scanner {
    library: Library,
    engine: *mut c_void,
    free: Free,
    version: String,
    signatures: String,
}

fn native_path(path: &Path) -> Result<CString> {
    CString::new(path.to_str().context("non-UTF-8 scanner path")?).context("NUL in scanner path")
}

impl Scanner {
    /// The parent enables scanning and supplies the library and definition paths.
    pub fn from_environment() -> Result<Option<Self>> {
        if std::env::var_os("MAILARCHIVER_SCAN").is_none() {
            return Ok(None);
        }
        let library_path =
            std::env::var_os("MAILARCHIVER_CLAMAV_LIBRARY").context("missing libclamav path")?;
        let definitions = PathBuf::from(
            std::env::var_os("MAILARCHIVER_CLAMAV_DATABASE").context("missing definitions")?,
        );
        // SAFETY: all symbols below follow the verified libclamav 1.x public C ABI.
        let library = unsafe { Library::new(library_path)? };
        let version = unsafe { CStr::from_ptr(library.get::<Version>(b"cl_retver\0")?()) }
            .to_str()?
            .to_owned();
        ensure!(
            version.starts_with("1."),
            "unsupported libclamav ABI: {version}"
        );
        let free = unsafe { *library.get::<Free>(b"cl_engine_free\0")? };
        let init = unsafe { library.get::<Init>(b"cl_init\0")?(0) };
        ensure!(init == 0, "libclamav initialization failed: {init}");
        let engine = unsafe { library.get::<New>(b"cl_engine_new\0")?() };
        ensure!(!engine.is_null(), "libclamav engine allocation failed");
        let mut scanner = Self {
            library,
            engine,
            free,
            version,
            signatures: String::new(),
        };
        if let Some(certs) = std::env::var_os("MAILARCHIVER_CLAMAV_CERTIFICATES") {
            let certs = native_path(Path::new(&certs))?;
            let status = unsafe {
                scanner.library.get::<SetStr>(b"cl_engine_set_str\0")?(engine, 37, certs.as_ptr())
            };
            scanner.check(status)?;
        }
        let mut count = 0;
        let mut versions = Vec::new();
        for name in ["main", "daily", "bytecode"] {
            let candidates: Vec<_> = ["cvd", "cld"]
                .iter()
                .map(|extension| definitions.join(format!("{name}.{extension}")))
                .filter(|path| path.is_file())
                .collect();
            ensure!(
                candidates.len() == 1,
                "expected one {name} definition archive"
            );
            let path = &candidates[0];
            let mut header = [0; 512];
            File::open(path)?.read_exact(&mut header)?;
            let fields: Vec<_> = std::str::from_utf8(&header)?.trim().split(':').collect();
            ensure!(
                fields.len() >= 9 && fields[0] == "ClamAV-VDB",
                "invalid definition header"
            );
            versions.push(format!("{name}:{}", fields[2].parse::<u32>()?));
            let path = native_path(path)?;
            let status = unsafe {
                scanner.library.get::<Load>(b"cl_load\0")?(
                    path.as_ptr(),
                    engine,
                    &mut count,
                    0x300a,
                )
            };
            scanner.check(status)?;
        }
        ensure!(count > 0, "no antivirus signatures loaded");
        scanner.signatures = versions.join(",");
        let status = unsafe { scanner.library.get::<Compile>(b"cl_engine_compile\0")?(engine) };
        scanner.check(status)?;
        Ok(Some(scanner))
    }

    fn error(&self, status: c_int) -> Result<String> {
        Ok(
            unsafe { CStr::from_ptr(self.library.get::<ErrorText>(b"cl_strerror\0")?(status)) }
                .to_string_lossy()
                .into_owned(),
        )
    }

    fn check(&self, status: c_int) -> Result<()> {
        ensure!(status == 0, "libclamav: {}", self.error(status)?);
        Ok(())
    }

    pub fn write_record(&self, spool: &mut File, output: &mut dyn Write) -> Result<()> {
        // Scan the RFC message: exclude the MBOX envelope
        // and record terminator, and undo exactly one level of mboxrd quoting.
        let mut raw = tempfile::NamedTempFile::new()?;
        spool.rewind()?;
        let length = spool.metadata()?.len();
        let mut reader = BufReader::new(&mut *spool);
        let mut line = Vec::new();
        let envelope = reader.read_until(b'\n', &mut line)? as u64;
        ensure!(length > envelope, "empty MBOX record");
        let mut message = BufReader::new(reader.take(length - envelope - 1));
        loop {
            line.clear();
            if message.read_until(b'\n', &mut line)? == 0 {
                break;
            }
            let unquoted = if line.starts_with(b">")
                && line
                    .iter()
                    .skip_while(|&&c| c == b'>')
                    .copied()
                    .take(5)
                    .eq(b"From ".iter().copied())
            {
                &line[1..]
            } else {
                &line
            };
            raw.write_all(unquoted)?;
        }
        raw.flush()?;
        let path = native_path(raw.path())?;
        let mut name = std::ptr::null();
        let mut options = ScanOptions {
            general: 4,
            parse: u32::MAX,
            heuristic: 0xc4,
            mail: 0,
            dev: 0,
        };
        let status = unsafe {
            self.library.get::<Scan>(b"cl_scanfile\0")?(
                path.as_ptr(),
                &mut name,
                std::ptr::null_mut(),
                self.engine,
                &mut options,
            )
        };
        let detail = if name.is_null() {
            self.error(status)?
        } else {
            unsafe { CStr::from_ptr(name) }
                .to_string_lossy()
                .into_owned()
        };
        ensure!(
            status == 0
                || (status == 1
                    && !detail.starts_with("Heuristics.Limits.Exceeded")
                    && !detail.starts_with("Heuristics.Encrypted")),
            "ClamAV scan failed: {detail}"
        );
        spool.rewind()?;
        if status == 1 {
            // Preserve the three required importer headers immediately after the envelope.
            let mut reader = BufReader::new(&mut *spool);
            for _ in 0..4 {
                line.clear();
                ensure!(
                    reader.read_until(b'\n', &mut line)? > 0,
                    "incomplete importer headers"
                );
                output.write_all(&line)?;
            }
            ensure!(
                !detail.contains(['\r', '\n']),
                "invalid ClamAV detection name"
            );
            write!(output, "X-ClamAV-Detection: {detail}\r\nX-ClamAV-Engine-Version: {}\r\nX-ClamAV-Definitions-Version: {}\r\n", self.version, self.signatures)?;
            std::io::copy(&mut reader, output)?;
        } else {
            std::io::copy(spool, output)?;
        }
        Ok(())
    }
}

impl Drop for Scanner {
    fn drop(&mut self) {
        // SAFETY: the engine was allocated above and is freed before unloading its library.
        unsafe {
            (self.free)(self.engine);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[ignore = "requires libclamav and definitions; run make test-clamav-rust"]
    fn native_scan_adds_headers_only_to_infected_records() -> Result<()> {
        // Requirement: infected-only provenance; no per-message hash or database.
        let scanner = Scanner::from_environment()?.context("scanning is not enabled")?;
        let prefix = b"From pst-importer Thu Jan  1 00:00:00 1970\nX-Imported-URI: pst:///fixture\r\nX-Importer-Name: pst-importer\r\nX-Importer-Version: 1\r\n";
        for body in [
            b"ordinary text".as_slice(),
            b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*",
        ] {
            let mut spool = tempfile::tempfile()?;
            let original = [
                prefix.as_slice(),
                b"From: sender@example.test\r\nSubject: scanner fixture\r\nMIME-Version: 1.0\r\nContent-Type: application/octet-stream\r\n\r\n",
                body,
                b"\r\n\n",
            ]
            .concat();
            spool.write_all(&original)?;
            let mut output = Vec::new();
            scanner.write_record(&mut spool, &mut output)?;
            if body == b"ordinary text" {
                assert_eq!(output, original);
            } else {
                let mail = mailparse::parse_mail(&output[prefix.len()..])?;
                use mailparse::MailHeaderMap;
                assert!(mail
                    .headers
                    .get_first_value("X-ClamAV-Detection")
                    .unwrap()
                    .contains("Eicar"));
                assert_eq!(
                    mail.headers
                        .get_first_value("X-ClamAV-Engine-Version")
                        .unwrap(),
                    scanner.version
                );
                assert_eq!(
                    mail.headers
                        .get_first_value("X-ClamAV-Definitions-Version")
                        .unwrap(),
                    scanner.signatures
                );
                let body_start = output
                    .windows(4)
                    .position(|bytes| bytes == b"\r\n\r\n")
                    .unwrap()
                    + 4;
                assert_eq!(&output[body_start..], [body, b"\r\n\n"].concat());
                assert!(output.starts_with(prefix));
            }
        }
        Ok(())
    }
}
