// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Exercise the built reader executables against purpose-made archive bytes.
// The demo CLI creates the real SQLite/MBOX fixture without Python or Make.
// The webview CLI dispatches actual JSON requests over its headless transport.
// Search and MIME selection must work at paths containing spaces and Unicode.
// A recursive file inventory detects changed bytes and unexpected sidecars.
// This tests native executable/core behavior, not native window interaction.
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    io::Write,
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

fn inventory(root: &Path) -> BTreeMap<PathBuf, Vec<u8>> {
    fn walk(root: &Path, path: &Path, files: &mut BTreeMap<PathBuf, Vec<u8>>) {
        for entry in fs::read_dir(path).unwrap() {
            let entry = entry.unwrap();
            if entry.file_type().unwrap().is_dir() {
                walk(root, &entry.path(), files);
            } else {
                files.insert(
                    entry.path().strip_prefix(root).unwrap().to_owned(),
                    Sha256::digest(fs::read(entry.path()).unwrap()).to_vec(),
                );
            }
        }
    }
    let mut files = BTreeMap::new();
    walk(root, root, &mut files);
    files
}

#[test]
fn executable_search_and_decode_leave_fixture_inventory_unchanged() {
    // Requirements: native CLI interoperability, verified decoding and no archive
    // writes, including at Windows drive-letter/space/non-ASCII paths.
    let temporary = tempfile::tempdir().unwrap();
    let archive = temporary.path().join("reader # café");
    let created = Command::new(env!("CARGO_BIN_EXE_mailsearch-rust"))
        .arg("--create-demo")
        .arg(&archive)
        .output()
        .unwrap();
    assert!(created.status.success(), "{:?}", created);
    let before = inventory(&archive);
    assert_eq!(before.len(), 3);
    let mut process = Command::new(env!("CARGO_BIN_EXE_mailsearch-webview"))
        .arg("--rpc")
        .arg(&archive)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let mut input = process.stdin.take().unwrap();
    for request in [
        serde_json::json!({"id":1,"method":"search","args":["observatory"]}),
        serde_json::json!({"id":2,"method":"message","args":[2]}),
        serde_json::json!({"id":3,"method":"part","args":[2,0]}),
        serde_json::json!({"id":4,"method":"message","args":[3]}),
        serde_json::json!({"id":5,"method":"part","args":[3,0]}),
    ] {
        writeln!(input, "{request}").unwrap();
    }
    drop(input);
    let output = process.wait_with_output().unwrap();
    assert!(output.status.success(), "{:?}", output);
    let replies: Vec<serde_json::Value> = String::from_utf8(output.stdout)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    assert_eq!(replies.len(), 5);
    for (index, reply) in replies.iter().enumerate() {
        assert_eq!(reply["id"], index + 1);
        assert!(reply["error"].is_null(), "{reply}");
    }
    assert_eq!(replies[0]["result"]["results"].as_array().unwrap().len(), 2);
    assert_eq!(replies[1]["result"]["subject"], "Café notes");
    assert!(replies[2]["result"]["content"]
        .as_str()
        .unwrap()
        .contains("Café lunch"));
    assert!(replies[4]["result"]["content"]
        .as_str()
        .unwrap()
        .contains("roses are blooming"));
    assert!(!replies[4]["result"]["content"]
        .as_str()
        .unwrap()
        .contains("never executed"));
    assert_eq!(inventory(&archive), before);
}
