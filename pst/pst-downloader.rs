// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

//! Corpus acquisition contract: pst/README.md. Does not import or rewrite mail.
use anyhow::{bail, ensure, Context, Result};
use clap::{Parser, ValueEnum};
use reqwest::{blocking::Client, redirect::Policy, Url};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File},
    io::{Read, Seek, Write},
    path::{Path, PathBuf},
    time::Duration,
};
use tempfile::NamedTempFile;

#[derive(Clone, Copy, Debug, Default, ValueEnum, PartialEq)]
enum Scope {
    Fixtures,
    Archives,
    #[default]
    All,
}
#[derive(Parser)]
#[command(
    version,
    about = "Download and verify PST test corpora without importing mail"
)]
struct Args {
    #[arg(long, default_value = "pst")]
    inventory_dir: PathBuf,
    #[arg(long, default_value = "var/pst")]
    output: PathBuf,
    #[arg(long, value_enum, default_value = "all")]
    scope: Scope,
    #[arg(long)]
    dry_run: bool,
    /// Stop after this many distinct source URLs.
    #[arg(long)]
    limit: Option<usize>,
    #[arg(long, default_value_t = 16 * 1024 * 1024 * 1024)]
    max_download_bytes: u64,
    #[arg(long, default_value_t = 64 * 1024 * 1024 * 1024)]
    max_expanded_bytes: u64,
    #[arg(long, default_value_t = 1800)]
    timeout_seconds: u64,
}
#[derive(Deserialize)]
struct Fixture {
    download_url: String,
    size_bytes: u64,
    sha256: String,
}
#[derive(Clone, Deserialize)]
struct Member {
    name: String,
    size: u64,
    sha256: String,
}
#[derive(Deserialize)]
struct Package {
    url: String,
    download_size_bytes: Option<u64>,
    zip_size: Option<u64>,
    #[serde(default)]
    files: Vec<Member>,
}
#[derive(Deserialize)]
struct Additional {
    download_url: String,
}
#[derive(Deserialize)]
struct Inventory {
    #[serde(default)]
    fixtures: Vec<Fixture>,
    #[serde(default)]
    enron_packages: Vec<Package>,
    #[serde(default)]
    enron_all_130_download_urls: Vec<String>,
    additional_documented_source: Option<Additional>,
}
#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
enum Kind {
    Pst,
    Zip,
    SevenZip,
}
#[derive(Clone, Eq, PartialEq, Ord, PartialOrd, Serialize, Deserialize)]
struct InventoryRef {
    path: String,
    sha256: String,
}
struct Job {
    url: String,
    kind: Kind,
    expected_size: Option<u64>,
    expected_sha256: Option<String>,
    members: BTreeMap<String, Member>,
    inventories: BTreeSet<InventoryRef>,
}
#[derive(Serialize, Deserialize)]
struct Receipt {
    url: String,
    final_url: String,
    sha256: String,
    size: u64,
}
#[derive(Serialize, Deserialize)]
struct Observation {
    url: String,
    member: Option<String>,
    path: PathBuf,
    sha256: String,
    size: u64,
    expected_sha256_verified: bool,
    inventories: BTreeSet<InventoryRef>,
}
#[derive(Serialize, Deserialize)]
struct Failure {
    url: String,
    error: String,
}
#[derive(Serialize, Deserialize, Default)]
struct Report {
    observations: Vec<Observation>,
    failures: Vec<Failure>,
    completed_urls: usize,
}

/// Return the lowercase SHA-256 digest of bytes already in memory.
fn sha256_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

/// Validate an inventory SHA-256 string and normalize its letter case.
fn normalize_sha256(text: &str) -> Result<String> {
    ensure!(
        text.len() == 64 && text.bytes().all(|b| b.is_ascii_hexdigit()),
        "invalid SHA-256"
    );
    Ok(text.to_ascii_lowercase())
}

/// Accept HTTPS sources and loopback HTTP tests without credentials or fragments.
fn valid_url(url: &Url) -> bool {
    url.username().is_empty()
        && url.password().is_none()
        && url.fragment().is_none()
        && (url.scheme() == "https"
            || (url.scheme() == "http" && matches!(url.host_str(), Some("127.0.0.1" | "[::1]"))))
}

/// Infer the supported artifact type from a validated source URL.
fn kind(url: &str) -> Result<Kind> {
    let url = Url::parse(url)?;
    ensure!(
        valid_url(&url),
        "only HTTPS (or loopback HTTP for tests) without credentials/fragments is accepted"
    );
    let path = url.path().to_ascii_lowercase();
    if path.ends_with(".pst") {
        Ok(Kind::Pst)
    } else if path.ends_with(".zip") {
        Ok(Kind::Zip)
    } else if path.ends_with(".7z") {
        Ok(Kind::SevenZip)
    } else {
        bail!("unsupported download suffix: {url}")
    }
}

/// Merge one inventory entry into the URL plan, rejecting conflicting expectations.
fn add_job(
    jobs: &mut BTreeMap<String, Job>,
    url: String,
    expected_size: Option<u64>,
    expected_sha256: Option<String>,
    members: Vec<Member>,
    inventory: &InventoryRef,
) -> Result<()> {
    let download_kind = kind(&url)?;
    let expected_sha256 = expected_sha256
        .as_deref()
        .map(normalize_sha256)
        .transpose()?;
    let job = jobs.entry(url.clone()).or_insert_with(|| Job {
        url,
        kind: download_kind,
        expected_size,
        expected_sha256: expected_sha256.clone(),
        members: BTreeMap::new(),
        inventories: BTreeSet::new(),
    });
    if let Some(expected_size) = expected_size {
        ensure!(
            job.expected_size.is_none_or(|v| v == expected_size),
            "conflicting sizes for {}",
            job.url
        );
        job.expected_size = Some(expected_size);
    }
    if let Some(expected_sha256) = expected_sha256 {
        ensure!(
            job.expected_sha256
                .as_ref()
                .is_none_or(|v| *v == expected_sha256),
            "conflicting digests for {}",
            job.url
        );
        job.expected_sha256 = Some(expected_sha256);
    }
    for mut member in members {
        member.sha256 = normalize_sha256(&member.sha256)?;
        if let Some(old) = job.members.get(&member.name) {
            ensure!(
                old.size == member.size && old.sha256 == member.sha256,
                "conflicting member metadata"
            );
        }
        job.members.insert(member.name.clone(), member);
    }
    job.inventories.insert(inventory.clone());
    Ok(())
}

/// Read inventories and build a sorted, deduplicated plan for the selected scope.
fn plan(args: &Args) -> Result<Vec<Job>> {
    let mut files = fs::read_dir(&args.inventory_dir)?
        .map(|entry| entry.map(|e| e.path()))
        .collect::<std::io::Result<Vec<_>>>()?;
    files.retain(|p| p.extension().is_some_and(|s| s == "json"));
    files.sort();
    ensure!(
        !files.is_empty(),
        "no inventory JSON files in {}",
        args.inventory_dir.display()
    );
    let mut jobs = BTreeMap::new();
    for path in files {
        let mut bytes = Vec::new();
        File::open(&path)?
            .take(16 * 1024 * 1024 + 1)
            .read_to_end(&mut bytes)?;
        ensure!(bytes.len() <= 16 * 1024 * 1024, "inventory exceeds 16 MiB");
        let inventory: Inventory =
            serde_json::from_slice(&bytes).with_context(|| format!("parse {}", path.display()))?;
        let name = InventoryRef {
            path: path.display().to_string(),
            sha256: sha256_hex(&bytes),
        };
        ensure!(
            !inventory.fixtures.is_empty()
                || !inventory.enron_packages.is_empty()
                || !inventory.enron_all_130_download_urls.is_empty()
                || inventory.additional_documented_source.is_some(),
            "{} contains no recognized download entries",
            path.display()
        );
        if args.scope != Scope::Archives {
            for fixture in inventory.fixtures {
                add_job(
                    &mut jobs,
                    fixture.download_url,
                    Some(fixture.size_bytes),
                    Some(fixture.sha256),
                    vec![],
                    &name,
                )?;
            }
        }
        if args.scope != Scope::Fixtures {
            for package in inventory.enron_packages {
                add_job(
                    &mut jobs,
                    package.url,
                    package.download_size_bytes.or(package.zip_size),
                    None,
                    package.files,
                    &name,
                )?;
            }
            for url in inventory.enron_all_130_download_urls {
                add_job(&mut jobs, url, None, None, vec![], &name)?;
            }
            if let Some(source) = inventory.additional_documented_source {
                add_job(&mut jobs, source.download_url, None, None, vec![], &name)?;
            }
        }
    }
    let mut jobs: Vec<_> = jobs.into_values().collect();
    if let Some(limit) = args.limit {
        jobs.truncate(limit);
    }
    Ok(jobs)
}

/// Copy bytes without hashing, enforcing the limit before each write.
fn copy_bounded(reader: &mut dyn Read, out: &mut dyn Write, limit: u64) -> Result<u64> {
    let mut size = 0u64;
    let mut buffer = [0u8; 65536];
    loop {
        let bytes_read = reader.read(&mut buffer)?;
        if bytes_read == 0 {
            break;
        }
        size = size
            .checked_add(bytes_read as u64)
            .context("byte count overflow")?;
        ensure!(size <= limit, "byte limit exceeded ({limit})");
        out.write_all(&buffer[..bytes_read])?;
    }
    Ok(size)
}

/// Check a completed file's size, optional PST signature and expected SHA-256.
fn check_file(
    path: &Path,
    expected_sha256: Option<&str>,
    expected_size: Option<u64>,
    limit: u64,
    require_pst_header: bool,
) -> Result<(String, u64)> {
    ensure!(
        fs::symlink_metadata(path)?.file_type().is_file(),
        "not a regular cached file: {}",
        path.display()
    );
    let mut file = File::open(path)?;
    ensure!(
        file.metadata()?.len() <= limit,
        "byte limit exceeded ({limit})"
    );
    if require_pst_header {
        let mut header = [0; 12];
        file.read_exact(&mut header).context("short PST header")?;
        ensure!(
            &header[..4] == b"!BDN" && &header[8..10] == b"SM",
            "not a PST header"
        );
        file.rewind()?;
    }
    let mut hasher = Sha256::new();
    let mut size = 0u64;
    let mut buffer = [0u8; 65536];
    loop {
        let bytes_read = file.read(&mut buffer)?;
        if bytes_read == 0 {
            break;
        }
        size = size
            .checked_add(bytes_read as u64)
            .context("byte count overflow")?;
        ensure!(size <= limit, "byte limit exceeded ({limit})");
        hasher.update(&buffer[..bytes_read]);
    }
    let sha256 = format!("{:x}", hasher.finalize());
    if let Some(expected_sha256) = expected_sha256 {
        ensure!(
            sha256 == expected_sha256,
            "SHA-256 mismatch for {}",
            path.display()
        );
    }
    if let Some(expected_size) = expected_size {
        ensure!(
            size == expected_size,
            "size mismatch: expected {expected_size}, received {size}"
        );
    }
    Ok((sha256, size))
}

/// Replace a JSON report atomically after flushing its temporary file.
fn atomic_json(path: &Path, value: &impl Serialize) -> Result<()> {
    let mut tmp = NamedTempFile::new_in(path.parent().context("report has no parent")?)?;
    serde_json::to_writer_pretty(&mut tmp, value)?;
    tmp.write_all(b"\n")?;
    tmp.as_file().sync_all()?;
    tmp.persist(path)?;
    Ok(())
}

/// Publish a verified temporary file, or validate the existing destination.
fn install(
    tmp: NamedTempFile,
    destination: &Path,
    expected_sha256: &str,
    expected_size: u64,
) -> Result<()> {
    if destination.exists() {
        check_file(
            destination,
            Some(expected_sha256),
            Some(expected_size),
            expected_size,
            false,
        )?;
    } else {
        tmp.as_file().sync_all()?;
        tmp.persist_noclobber(destination)?;
    }
    Ok(())
}

/// Reuse a verified artifact or download and validate it before caching.
fn acquire(job: &Job, args: &Args, client: &Client) -> Result<PathBuf> {
    let folder = args
        .output
        .join("downloads")
        .join(sha256_hex(job.url.as_bytes()));
    fs::create_dir_all(&folder)?;
    let path = folder.join(match job.kind {
        Kind::Pst => "source.pst",
        Kind::Zip => "source.zip",
        Kind::SevenZip => "source.7z",
    });
    let receipt_path = folder.join("receipt.json");
    if path.exists() {
        let receipt: Receipt = serde_json::from_reader(File::open(&receipt_path).context(
            "cached artifact lacks receipt; preserve it and move it aside before retrying",
        )?)?;
        ensure!(receipt.url == job.url, "cached receipt URL mismatch");
        check_file(
            &path,
            Some(&receipt.sha256),
            Some(receipt.size),
            args.max_download_bytes,
            job.kind == Kind::Pst,
        )?;
        if let Some(expected_sha256) = &job.expected_sha256 {
            ensure!(
                *expected_sha256 == receipt.sha256,
                "inventory digest differs from cache"
            );
        }
        if let Some(size) = job.expected_size {
            ensure!(size == receipt.size, "inventory size differs from cache");
        }
        return Ok(path);
    }
    if let Some(size) = job.expected_size {
        ensure!(
            size <= args.max_download_bytes,
            "inventory size exceeds download limit"
        );
    }
    let mut response = client.get(&job.url).send()?.error_for_status()?;
    ensure!(
        response.status() == reqwest::StatusCode::OK,
        "expected complete HTTP 200 response, got {}",
        response.status()
    );
    if let Some(length) = response.content_length() {
        ensure!(
            length <= args.max_download_bytes,
            "HTTP length exceeds limit"
        );
    }
    let final_url = response.url().to_string();
    let mut tmp = NamedTempFile::new_in(&folder)?;
    // These test fixtures are small enough that a separate hashing pass is inexpensive.
    // Hash the completed file during validation instead of also hashing the download.
    // Keep I/O buffered because the inventory also includes larger Enron archives.
    copy_bounded(&mut response, &mut tmp, args.max_download_bytes)?;
    let (sha256, size) = check_file(
        tmp.path(),
        job.expected_sha256.as_deref(),
        job.expected_size,
        args.max_download_bytes,
        job.kind == Kind::Pst,
    )?;
    if job.kind != Kind::Pst {
        let mut magic = [0; 6];
        tmp.rewind()?;
        tmp.read_exact(&mut magic)?;
        ensure!(
            match job.kind {
                Kind::Zip => magic.starts_with(b"PK\x03\x04") || magic.starts_with(b"PK\x05\x06"),
                Kind::SevenZip => magic == [0x37, 0x7a, 0xbc, 0xaf, 0x27, 0x1c],
                Kind::Pst => true,
            },
            "response is not the expected archive type"
        );
    }
    install(tmp, &path, &sha256, size)?;
    atomic_json(
        &receipt_path,
        &Receipt {
            url: job.url.clone(),
            final_url,
            sha256,
            size,
        },
    )?;
    Ok(path)
}

struct ExpectedPst<'a> {
    member_name: Option<&'a str>,
    expected_size: Option<u64>,
    expected_sha256: Option<&'a str>,
}

/// Copy and validate a PST, then record its content-addressed object and provenance.
fn save_pst(
    reader: &mut dyn Read,
    job: &Job,
    expected_pst: ExpectedPst<'_>,
    remaining: &mut u64,
    args: &Args,
    report: &mut Report,
) -> Result<()> {
    if let Some(size) = expected_pst.expected_size {
        ensure!(size <= *remaining, "expanded size exceeds remaining limit");
    }
    let mut tmp = NamedTempFile::new_in(args.output.join("objects"))?;
    let copied_size = copy_bounded(reader, &mut tmp, *remaining)?;
    *remaining -= copied_size;
    let (sha256, size) = check_file(
        tmp.path(),
        expected_pst.expected_sha256,
        expected_pst.expected_size,
        copied_size,
        true,
    )?;
    let relative = PathBuf::from("objects").join(format!("{sha256}.pst"));
    install(tmp, &args.output.join(&relative), &sha256, size)?;
    report.observations.push(Observation {
        url: job.url.clone(),
        member: expected_pst.member_name.map(str::to_owned),
        path: relative,
        sha256,
        size,
        expected_sha256_verified: expected_pst.expected_sha256.is_some(),
        inventories: job.inventories.clone(),
    });
    Ok(())
}

/// Reject archive member names that imply traversal or platform-specific paths.
fn safe_member(name: &str) -> Result<()> {
    ensure!(
        !name.starts_with(['/', '\\'])
            && !name.contains(':')
            && !name.split(['/', '\\']).any(|p| p == ".."),
        "unsafe archive member name: {name}"
    );
    Ok(())
}

/// Collect PSTs from an artifact while enforcing member checks and expansion limits.
fn extract(job: &Job, path: &Path, args: &Args, report: &mut Report) -> Result<()> {
    let mut remaining = args.max_expanded_bytes;
    let mut seen = BTreeSet::new();
    let before = report.observations.len();
    match job.kind {
        Kind::Pst => save_pst(
            &mut File::open(path)?,
            job,
            ExpectedPst {
                member_name: None,
                expected_size: job.expected_size,
                expected_sha256: job.expected_sha256.as_deref(),
            },
            &mut remaining,
            args,
            report,
        )?,
        Kind::Zip => {
            let mut archive = zip::ZipArchive::new(File::open(path)?)?;
            for index in 0..archive.len() {
                let mut member = archive.by_index(index)?;
                let name = member.name().to_owned();
                safe_member(&name)?;
                ensure!(
                    member.size() <= remaining,
                    "archive expansion exceeds limit"
                );
                if !member.is_dir() && name.to_ascii_lowercase().ends_with(".pst") {
                    ensure!(
                        member
                            .unix_mode()
                            .is_none_or(|mode| mode & 0o170000 != 0o120000),
                        "PST member is a symlink"
                    );
                    ensure!(seen.insert(name.clone()), "duplicate PST member name");
                    let expected_member = job.members.get(&name);
                    if let Some(expected_member) = expected_member {
                        ensure!(
                            expected_member.size == member.size(),
                            "inventory member size mismatch"
                        );
                    }
                    let size = member.size();
                    save_pst(
                        &mut member,
                        job,
                        ExpectedPst {
                            member_name: Some(&name),
                            expected_size: Some(size),
                            expected_sha256: expected_member.map(|member| member.sha256.as_str()),
                        },
                        &mut remaining,
                        args,
                        report,
                    )?;
                } else {
                    remaining -= member.size();
                }
            }
        }
        Kind::SevenZip => {
            let mut archive =
                sevenz_rust::SevenZReader::open(path, sevenz_rust::Password::empty())?;
            archive.for_each_entries(|member, reader| {
                (|| -> Result<bool> {
                    safe_member(member.name())?;
                    ensure!(
                        !member.has_windows_attributes || member.windows_attributes & 0x400 == 0,
                        "7z reparse point rejected"
                    );
                    ensure!(
                        member.size() <= remaining,
                        "archive expansion exceeds limit"
                    );
                    if !member.is_directory()
                        && member.name().to_ascii_lowercase().ends_with(".pst")
                    {
                        ensure!(!member.is_anti_item, "7z anti-item cannot be a fixture");
                        ensure!(
                            seen.insert(member.name().to_owned()),
                            "duplicate PST member name"
                        );
                        let expected_member = job.members.get(member.name());
                        if let Some(expected_member) = expected_member {
                            ensure!(
                                expected_member.size == member.size(),
                                "inventory member size mismatch"
                            );
                        }
                        save_pst(
                            reader,
                            job,
                            ExpectedPst {
                                member_name: Some(member.name()),
                                expected_size: Some(member.size()),
                                expected_sha256: expected_member
                                    .map(|member| member.sha256.as_str()),
                            },
                            &mut remaining,
                            args,
                            report,
                        )?;
                    } else {
                        let size = copy_bounded(reader, &mut std::io::sink(), remaining)?;
                        remaining -= size;
                    }
                    Ok(true)
                })()
                .map_err(|error| sevenz_rust::Error::other(format!("{error:#}")))
            })?;
        }
    }
    ensure!(
        report.observations.len() > before,
        "archive contains no PST files"
    );
    for member in job.members.keys() {
        ensure!(
            seen.contains(member),
            "expected PST member missing: {member}"
        );
    }
    Ok(())
}

/// Execute the selected plan under a cache lock and retain per-source results.
fn run(args: Args) -> Result<bool> {
    ensure!(
        args.timeout_seconds > 0 && args.max_download_bytes > 0 && args.max_expanded_bytes > 0,
        "limits must be positive"
    );
    let jobs = plan(&args)?;
    println!(
        "{} distinct URLs; {} have no published size; known download bytes: {}",
        jobs.len(),
        jobs.iter().filter(|j| j.expected_size.is_none()).count(),
        jobs.iter().filter_map(|j| j.expected_size).sum::<u64>()
    );
    if args.dry_run {
        for job in jobs {
            println!("{:?}\t{}", job.kind, job.url);
        }
        return Ok(true);
    }
    fs::create_dir_all(args.output.join("objects"))?;
    let lock_path = args.output.join(".download.lock");
    let lock = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&lock_path)
        .context("cache is locked; inspect stale .download.lock before removing it")?;
    let result = (|| -> Result<bool> {
        fs::create_dir_all(args.output.join("reports"))?;
        let (report_file, report_path) = tempfile::Builder::new()
            .prefix("run-")
            .suffix(".json")
            .tempfile_in(args.output.join("reports"))?
            .keep()?;
        drop(report_file);
        let client = Client::builder()
            .connect_timeout(Duration::from_secs(30))
            .timeout(Duration::from_secs(args.timeout_seconds))
            .redirect(Policy::custom(|attempt| {
                if attempt.previous().len() >= 10 || !valid_url(attempt.url()) {
                    attempt.error("unsafe URL or redirect limit")
                } else {
                    attempt.follow()
                }
            }))
            .user_agent("MCT-pst-downloader/1.0")
            .build()?;
        let mut report = Report::default();
        for (index, job) in jobs.iter().enumerate() {
            eprintln!("[{}/{}] {}", index + 1, jobs.len(), job.url);
            let result = (|| -> Result<()> {
                if let Some(expected_sha256) = &job.expected_sha256 {
                    let relative = PathBuf::from("objects").join(format!("{expected_sha256}.pst"));
                    let path = args.output.join(&relative);
                    if path.exists() {
                        let (_, size) = check_file(
                            &path,
                            Some(expected_sha256),
                            job.expected_size,
                            args.max_download_bytes.min(args.max_expanded_bytes),
                            true,
                        )?;
                        report.observations.push(Observation {
                            url: job.url.clone(),
                            member: None,
                            path: relative,
                            sha256: expected_sha256.clone(),
                            size,
                            expected_sha256_verified: true,
                            inventories: job.inventories.clone(),
                        });
                        return Ok(());
                    }
                }
                let path = acquire(job, &args, &client)?;
                extract(job, &path, &args, &mut report)
            })();
            match result {
                Ok(()) => report.completed_urls += 1,
                Err(error) => {
                    eprintln!("ERROR: {error:#}");
                    report.failures.push(Failure {
                        url: job.url.clone(),
                        error: format!("{error:#}"),
                    });
                }
            }
            atomic_json(&report_path, &report)?;
            atomic_json(&args.output.join("download-report.json"), &report)?;
        }
        atomic_json(&report_path, &report)?;
        atomic_json(&args.output.join("download-report.json"), &report)?;
        println!(
            "Completed URLs: {}; PST observations: {}; failed URLs: {}",
            report.completed_urls,
            report.observations.len(),
            report.failures.len()
        );
        Ok(report.failures.is_empty())
    })();
    drop(lock);
    fs::remove_file(lock_path)?;
    result
}

/// Parse CLI arguments and translate download results into process exit status.
fn main() -> std::process::ExitCode {
    match run(Args::parse()) {
        Ok(true) => std::process::ExitCode::SUCCESS,
        Ok(false) => std::process::ExitCode::FAILURE,
        Err(error) => {
            eprintln!("pst-downloader: {error:#}");
            std::process::ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests;
