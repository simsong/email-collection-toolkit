// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Order Rust validation consistently across Windows, macOS and Linux.
// Cargo aliases invoke this executable for reader-only or workspace checks.
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
    let scope = match args.as_slice() {
        [command, scope]
            if command == "check" && matches!(scope.as_str(), "reader" | "workspace") =>
        {
            scope
        }
        _ => {
            eprintln!("Usage: cargo reader-check | cargo workspace-check");
            return ExitCode::from(2);
        }
    };
    let workspace = scope == "workspace";
    let package = if workspace {
        vec!["--workspace"]
    } else {
        vec!["-p", "mailsearch-rust"]
    };
    let format = if workspace {
        vec!["--all"]
    } else {
        package.clone()
    };
    let cargo = env::var_os("CARGO").unwrap_or_else(|| "cargo".into());
    for args in [
        [vec!["fmt"], format, vec!["--", "--check"]].concat(),
        [
            vec!["clippy", "--locked"],
            package.clone(),
            vec!["--all-targets", "--", "-D", "warnings"],
        ]
        .concat(),
        [vec!["test", "--locked"], package, vec![]].concat(),
    ] {
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
