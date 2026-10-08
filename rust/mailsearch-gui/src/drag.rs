// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Prepare explicit message drags without exporting mail during browsing.
// One message becomes an exact verified EML; multiple messages become a ZIP.
// Read and verify one bounded record at a time, writing into private temporary files.
// Opaque tokens resolve only to exports registered by this process, never arbitrary paths.
// The bridge and cleanup registry retain failed paths until deletion succeeds.
// The macOS adapter replaces WebKit text writers with a single file URL writer.
use crate::Archive;
use anyhow::{ensure, Context, Result};
use serde_json::{json, Value};
use std::{
    collections::{HashMap, HashSet},
    fs::File,
    io::Write,
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex, OnceLock,
    },
};

#[cfg(target_os = "macos")]
#[path = "drag_macos.rs"]
mod macos;
#[cfg(all(target_os = "macos", feature = "native-smoke"))]
pub use macos::inspect;
#[cfg(target_os = "macos")]
pub use macos::install;

#[derive(Default)]
struct Registry {
    tokens: HashMap<String, PathBuf>,
    roots: HashSet<PathBuf>,
}
static CLOSING: AtomicBool = AtomicBool::new(false);
fn registry() -> &'static Mutex<Registry> {
    static REGISTRY: OnceLock<Mutex<Registry>> = OnceLock::new();
    REGISTRY.get_or_init(Mutex::default)
}

pub fn available() -> bool {
    #[cfg(target_os = "macos")]
    return macos::available();
    #[cfg(not(target_os = "macos"))]
    false
}

#[cfg(any(target_os = "macos", test))]
pub(crate) fn resolve(token: &str) -> Option<PathBuf> {
    registry()
        .lock()
        .ok()?
        .tokens
        .get(token)
        .filter(|path| path.is_file())
        .cloned()
}

pub(crate) struct Exports {
    directory: tempfile::TempDir,
    tokens: Vec<String>,
}
impl Exports {
    pub fn close(&mut self) -> Result<()> {
        let mut paths = registry()
            .lock()
            .map_err(|_| anyhow::anyhow!("Drag registry unavailable"))?;
        for token in &self.tokens {
            paths.tokens.remove(token);
        }
        drop(paths);
        match std::fs::remove_dir_all(self.directory.path()) {
            Ok(()) => (),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
            Err(error) => return Err(error).context("Clean temporary drag exports"),
        }
        registry()
            .lock()
            .map_err(|_| anyhow::anyhow!("Drag registry unavailable"))?
            .roots
            .remove(self.directory.path());
        Ok(())
    }
    pub fn new(archive: &Archive) -> Result<Self> {
        let temporary = std::env::temp_dir().canonicalize()?;
        ensure!(
            !temporary.starts_with(archive.root.canonicalize()?),
            "Temporary exports must be outside the archive"
        );
        let mut paths = registry()
            .lock()
            .map_err(|_| anyhow::anyhow!("Drag registry unavailable"))?;
        ensure!(!CLOSING.load(Ordering::Acquire), "Reader is closing");
        let directory = tempfile::tempdir_in(temporary)?;
        paths.roots.insert(directory.path().to_owned());
        Ok(Self {
            directory,
            tokens: vec![],
        })
    }
    pub fn prepare(&mut self, archive: &Archive, ids: &[i64]) -> Result<Value> {
        ensure!(
            !ids.is_empty() && ids.len() <= 1000,
            "Select between 1 and 1000 messages to drag"
        );
        let mut seen = HashSet::new();
        let unique: Vec<_> = ids.iter().copied().filter(|id| seen.insert(*id)).collect();
        let directory = tempfile::Builder::new()
            .prefix("drag-")
            .tempdir_in(self.directory.path())?;
        let filename = if unique.len() == 1 {
            format!("Message-{}.eml", unique[0])
        } else {
            format!("Email Collection Toolkit Messages ({}).zip", unique.len())
        };
        let destination = directory.path().join(&filename);
        let mut file = File::create(&destination)?;
        ensure!(!CLOSING.load(Ordering::Acquire), "Reader is closing");
        if unique.len() == 1 {
            file.write_all(&archive.raw_message(unique[0])?)?;
        } else {
            let mut zip = zip::ZipWriter::new(file);
            for id in unique {
                ensure!(!CLOSING.load(Ordering::Acquire), "Reader is closing");
                let raw = archive.raw_message(id)?;
                zip.start_file(
                    format!("Message-{id}.eml"),
                    zip::write::SimpleFileOptions::default()
                        .compression_method(zip::CompressionMethod::Stored),
                )?;
                zip.write_all(&raw)?;
            }
            file = zip.finish()?;
        }
        file.sync_all()?;
        let token = format!(
            "mailarchiver-export:{}-{}",
            self.directory
                .path()
                .file_name()
                .context("Missing export directory name")?
                .to_string_lossy(),
            directory
                .path()
                .file_name()
                .context("Missing drag directory name")?
                .to_string_lossy()
        );
        let mut paths = registry()
            .lock()
            .map_err(|_| anyhow::anyhow!("Drag registry unavailable"))?;
        ensure!(!CLOSING.load(Ordering::Acquire), "Reader is closing");
        paths.tokens.insert(token.clone(), destination);
        self.tokens.push(token.clone());
        let _retained = directory.keep();
        Ok(json!({"filename":filename,"token":token}))
    }
}
impl Drop for Exports {
    fn drop(&mut self) {
        if let Err(error) = self.close() {
            eprintln!("{error:#}");
        }
    }
}

pub fn resume() {
    // Called only after both the archive owner and asynchronous cleanup finish.
    CLOSING.store(false, Ordering::Release);
}

pub fn close(finished: impl FnOnce(Result<()>) + Send + 'static) {
    // One native reader lives in each process today. Revoke before shutdown and
    // clean private exports independently of a potentially unfinished read worker.
    let roots = if let Ok(mut paths) = registry().lock() {
        CLOSING.store(true, Ordering::Release);
        paths.tokens.clear();
        paths.roots.iter().cloned().collect::<Vec<_>>()
    } else {
        finished(Err(anyhow::anyhow!("Temporary drag registry lock failed")));
        return;
    };
    std::thread::spawn(move || {
        let mut errors = vec![];
        for root in roots {
            if let Err(error) = std::fs::remove_dir_all(&root) {
                if error.kind() != std::io::ErrorKind::NotFound {
                    errors.push(format!("{}: {error}", root.display()));
                    continue;
                }
            }
            match registry().lock() {
                Ok(mut paths) => {
                    paths.roots.remove(&root);
                }
                Err(_) => errors.push("Temporary drag registry lock failed".into()),
            }
        }
        finished(if errors.is_empty() {
            Ok(())
        } else {
            Err(anyhow::anyhow!(
                "Temporary drag cleanup failed: {}",
                errors.join("; ")
            ))
        });
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;
    #[test]
    fn cleanup_finishes_without_waiting_for_the_export_owner() {
        // Requirement: cleanup and cancellation do not wait for the archive worker.
        // A fresh child isolates the process-wide shutdown latch from other tests.
        let status = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "drag::tests::cleanup_child", "--ignored"])
            .status()
            .unwrap();
        assert!(status.success());
    }
    #[test]
    #[ignore = "isolated shutdown fixture invoked by the parent regression"]
    fn cleanup_child() {
        let fixture = tempfile::tempdir().unwrap();
        let path = fixture.path().join("archive");
        crate::demo::create(&path).unwrap();
        let archive = Archive::open(&path).unwrap();
        let before = std::fs::read(path.join("data/mbox/DEMO.mbox")).unwrap();
        let mut owner = Exports::new(&archive).unwrap();
        let prepared = owner.prepare(&archive, &[1, 2, 3]).unwrap();
        let token = prepared["token"].as_str().unwrap();
        let exported = resolve(token).unwrap();
        let (sender, receiver) = std::sync::mpsc::channel();
        close(move |result| sender.send(result).unwrap());
        receiver
            .recv_timeout(std::time::Duration::from_secs(10))
            .unwrap()
            .unwrap();
        // Keep the worker-owned Exports alive through successful cleanup.
        assert!(!exported.exists() && !owner.directory.path().exists());
        assert!(resolve(token).is_none());
        assert!(owner.prepare(&archive, &[1]).is_err());
        assert!(Exports::new(&archive).is_err());
        drop(owner);
        resume();
        let mut owner = Exports::new(&archive).unwrap();
        let prepared = owner.prepare(&archive, &[1, 2, 3]).unwrap();
        let token = prepared["token"].as_str().unwrap();
        let exported = resolve(token).unwrap();
        // Actual deletion failure retains the root even after its owner is dropped.
        let failed = owner.directory.path().to_owned();
        let saved = fixture.path().join("saved-drag-root");
        std::fs::rename(&failed, &saved).unwrap();
        std::fs::write(&failed, b"Deletion obstruction").unwrap();
        let (sender, receiver) = std::sync::mpsc::channel();
        close(move |result| sender.send(result).unwrap());
        assert!(receiver
            .recv_timeout(std::time::Duration::from_secs(10))
            .unwrap()
            .is_err());
        assert!(registry().lock().unwrap().roots.contains(&failed));
        assert!(resolve(token).is_none());
        assert!(owner.close().is_err());
        drop(owner);
        assert!(registry().lock().unwrap().roots.contains(&failed));
        std::fs::remove_file(&failed).unwrap();
        std::fs::rename(saved, &failed).unwrap();
        let (sender, receiver) = std::sync::mpsc::channel();
        close(move |result| sender.send(result).unwrap());
        receiver
            .recv_timeout(std::time::Duration::from_secs(10))
            .unwrap()
            .unwrap();
        assert!(!exported.exists() && !failed.exists());
        assert!(!registry().lock().unwrap().roots.contains(&failed));
        assert!(resolve(token).is_none());
        assert!(Exports::new(&archive).is_err());
        // A canceled update resumes a new owner, never the revoked export token.
        resume();
        let mut recovered = Exports::new(&archive).unwrap();
        let replacement = recovered.prepare(&archive, &[1, 2, 3]).unwrap();
        let replacement_token = replacement["token"].as_str().unwrap();
        assert_ne!(replacement_token, token);
        assert!(resolve(replacement_token).unwrap().exists());
        assert!(resolve(token).is_none());
        assert_eq!(
            std::fs::read(path.join("data/mbox/DEMO.mbox")).unwrap(),
            before
        );
    }
    #[test]
    fn drags_verify_exact_bytes_deduplicate_and_revoke_exports() {
        // Requirement: explicit EML/ZIP drags preserve canonical bytes and expire on close.
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("archive");
        crate::demo::create(&path).unwrap();
        let archive = Archive::open(&path).unwrap();
        let mbox = std::fs::read(path.join("data/mbox/DEMO.mbox")).unwrap();
        let mut exports = Exports::new(&archive).unwrap();
        let eml = exports.prepare(&archive, &[1, 1]).unwrap();
        let token = eml["token"].as_str().unwrap();
        let eml_path = resolve(token).unwrap();
        assert_eq!(std::fs::read(&eml_path).unwrap(), crate::demo::MESSAGES[0]);
        let bundle = exports.prepare(&archive, &[3, 1, 3, 2]).unwrap();
        let zip_path = resolve(bundle["token"].as_str().unwrap()).unwrap();
        let mut zip = zip::ZipArchive::new(File::open(&zip_path).unwrap()).unwrap();
        assert_eq!(zip.len(), 3);
        for id in 1..=3 {
            let mut raw = vec![];
            zip.by_name(&format!("Message-{id}.eml"))
                .unwrap()
                .read_to_end(&mut raw)
                .unwrap();
            assert_eq!(raw, crate::demo::MESSAGES[id - 1]);
        }
        drop(zip);
        assert!(exports.prepare(&archive, &[]).is_err());
        assert!(exports.prepare(&archive, &[1, 999]).is_err());
        assert!(exports.prepare(&archive, &vec![1; 1001]).is_err());
        assert_eq!(
            std::fs::read(path.join("data/mbox/DEMO.mbox")).unwrap(),
            mbox
        );
        assert!(resolve("file:///etc/passwd").is_none());
        let mut corrupt = mbox.clone();
        let last = corrupt.len() - 2;
        corrupt[last] ^= 1;
        std::fs::write(path.join("data/mbox/DEMO.mbox"), corrupt).unwrap();
        assert!(exports.prepare(&archive, &[1, 3]).is_err());
        assert_eq!(exports.tokens.len(), 2);
        assert_eq!(
            std::fs::read_dir(exports.directory.path()).unwrap().count(),
            2
        );
        std::fs::write(path.join("data/mbox/DEMO.mbox"), mbox).unwrap();
        drop(exports);
        assert!(resolve(token).is_none());
        assert!(!eml_path.exists() && !zip_path.exists());
    }
}
