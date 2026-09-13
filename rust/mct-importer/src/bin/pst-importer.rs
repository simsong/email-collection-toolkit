// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

//! MCT Importer API 1.0 PST executable; see doc/PST_IMPORTER.md.
use std::{io, path::Path, process::ExitCode};
fn main() -> ExitCode {
    let mut args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() == 1 {
        match args[0].to_str() {
            Some("--help" | "-h") => {
                println!("Usage: pst-importer [--] FILENAME\nRead a PST without modification and emit MCT Importer API 1.0 mboxrd on stdout.\nDiagnostics and completion counts go to stderr; nonzero status means incomplete extraction.");
                return ExitCode::SUCCESS;
            }
            Some("--version") => {
                println!(
                    "pst-importer {} (Microsoft outlook-pst 1.2.0)",
                    env!("CARGO_PKG_VERSION")
                );
                return ExitCode::SUCCESS;
            }
            Some("--api-version") => {
                println!("MCT Importer API {}", mct_importer::API_VERSION);
                return ExitCode::SUCCESS;
            }
            _ => {}
        }
    }
    if args.first().is_some_and(|a| a == "--") {
        args.remove(0);
    }
    if args.len() != 1 {
        eprintln!("Usage: pst-importer [--] FILENAME");
        return ExitCode::from(2);
    }
    match mct_importer::pst::import(
        Path::new(&args[0]),
        &mut io::BufWriter::new(io::stdout().lock()),
        &mut io::stderr().lock(),
    ) {
        Ok(report) if report.errors == 0 => ExitCode::SUCCESS,
        Ok(_) => ExitCode::FAILURE,
        Err(error) => {
            eprintln!("pst-importer: incomplete extraction: {error:#}");
            ExitCode::FAILURE
        }
    }
}
