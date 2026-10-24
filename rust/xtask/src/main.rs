// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Order Rust validation consistently across Windows, macOS and Linux.
// Cargo aliases invoke this executable for supported workspace checks.
// Each stage invokes Cargo directly, preserving its environment and toolchain.
// Formatting precedes Clippy, which precedes real unit and integration tests.
// The first failed stage stops the workflow and preserves its exit status.
// No shell interpolation, archive mutation or tool installation occurs here.
use std::{
    env,
    process::{Command, ExitCode},
};

fn main() -> ExitCode {
    let args: Vec<String> = env::args().skip(1).collect();
    if args.first().is_some_and(|arg| arg == "msix-test") {
        return match Command::new("pwsh")
            .args(["-NoProfile", "-File", "scripts/win/build_windows_msix.ps1"])
            .args(&args[1..])
            .status()
        {
            Ok(status) if status.success() => ExitCode::SUCCESS,
            _ => ExitCode::FAILURE,
        };
    }
    if args.as_slice() != ["check", "workspace"] {
        eprintln!("Usage: cargo workspace-check | cargo msix-test");
        return ExitCode::from(2);
    }
    let cargo = env::var_os("CARGO").unwrap_or_else(|| "cargo".into());
    for (command, suffix) in [
        ("fmt", &["--", "--check"][..]),
        ("clippy", &["--all-targets", "--", "-D", "warnings"][..]),
        ("test", &[][..]),
    ] {
        let mut args = vec![command];
        if command == "fmt" {
            args.push("--all");
        } else {
            args.extend_from_slice(&["--locked", "--workspace"]);
        }
        args.extend_from_slice(suffix);
        eprintln!("cargo {}", args.join(" "));
        match Command::new(&cargo).args(args).status() {
            Ok(status) if status.success() => (),
            Ok(status) => {
                return ExitCode::from(
                    status
                        .code()
                        .and_then(|code| u8::try_from(code).ok())
                        .filter(|code| *code != 0)
                        .unwrap_or(1),
                )
            }
            Err(error) => {
                eprintln!("Could not run Cargo: {error}");
                return ExitCode::FAILURE;
            }
        }
    }
    ExitCode::SUCCESS
}
