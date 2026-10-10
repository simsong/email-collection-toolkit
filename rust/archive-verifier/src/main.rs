// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
//! Provide a read-only command for checking imported archive database records.
//! Accept one archive directory and invoke the independent Rust verifier.
//! Print counts only after all catalog, MBOX and search checks succeed.
//! Errors return failure; usage errors are distinct from invalid archives.
//! This supplements the portable BagIt verifier rather than replacing it.
//! Run through make verify-database ARCHIVE=/path/to/archive.

use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() == 1 && (args[0] == "--help" || args[0] == "-h") {
        println!("Usage: archive-verifier ARCHIVE\nRead-only catalog/MBOX/search consistency verification. Stop all archive writers first.\nDoes not verify semantic digests, extracted text, processing.sqlite3, or source completeness.\nRun the portable archive verifier separately for complete BagIt checks.");
        return ExitCode::SUCCESS;
    }
    if args.len() != 1 {
        eprintln!("Usage: archive-verifier ARCHIVE");
        return ExitCode::from(2);
    }
    match archive_verifier::verify_archive(std::path::Path::new(&args[0])) {
        Ok(report) => {
            println!("Database import verified: {} messages, {} mailboxes, {} observations, {} indexed messages.",
                     report.messages, report.mailboxes, report.observations, report.indexed_messages);
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("Database import verification failed: {error:#}");
            ExitCode::FAILURE
        }
    }
}
