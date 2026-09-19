// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
//! Scan an external converter's complete mboxrd records without loading its reader.
use anyhow::{ensure, Context, Result};
use mct_importer::antivirus::Scanner;
use std::io::{self, BufRead, Read, Seek, Write};

fn scan() -> Result<()> {
    let mut args = std::env::args_os().skip(1);
    let input = args.next().context("usage: mcti-scan INPUT.mboxrd")?;
    ensure!(args.next().is_none(), "usage: mcti-scan INPUT.mboxrd");
    let scanner = Scanner::from_environment()?.context("scanning is not enabled")?;
    let mut source = io::BufReader::new(std::fs::File::open(input)?);
    let mut output = io::BufWriter::new(io::stdout().lock());
    let mut record = tempfile::tempfile()?;
    let mut line = Vec::new();
    let mut started = false;
    loop {
        line.clear();
        if (&mut source)
            .take(1024 * 1024 + 1)
            .read_until(b'\n', &mut line)?
            == 0
        {
            break;
        }
        ensure!(line.len() <= 1024 * 1024, "mboxrd line exceeds 1 MiB");
        if line.starts_with(b"From ") {
            if started {
                scanner.write_record(&mut record, &mut output)?;
                output.flush()?;
                record.set_len(0)?;
                record.rewind()?;
            }
            started = true;
        }
        ensure!(started, "input does not start with an mboxrd envelope");
        record.write_all(&line)?;
    }
    if started {
        scanner.write_record(&mut record, &mut output)?;
    }
    output.flush()?;
    Ok(())
}

fn main() -> std::process::ExitCode {
    match scan() {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("mcti-scan: {error:#}");
            std::process::ExitCode::FAILURE
        }
    }
}
