// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
//! Independently recover the SHA-256 identity of catalogued MBOX records.
//! Read bounded chunks, never entire messages or attachments into memory.
//! Try reversible mboxrd and the documented bounded legacy mboxo ambiguity.
//! Adopted envelopes and writer-added terminal newlines are hash-selected.
//! This module reads only; it neither parses MIME nor rewrites stored mail.
//! It supplies the byte-preservation check used by the catalog verifier.

use anyhow::{ensure, Result};
use sha2::{Digest, Sha256};
use std::fs::File;
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom};

#[derive(Clone, Copy)]
enum Quoting {
    Stored,
    Mboxrd,
    MboxoAll,
    MboxoMask(u16),
}

// Keep the final two bytes unhashed so LF/CRLF removal needs no second buffer.
struct Hashes {
    hash: Sha256,
    tail: Vec<u8>,
}

impl Hashes {
    fn new(prefix: &[u8]) -> Self {
        Self {
            hash: Sha256::new(),
            tail: prefix.to_vec(),
        }
    }

    fn write(&mut self, bytes: &[u8]) {
        self.tail.extend_from_slice(bytes);
        if self.tail.len() > 2 {
            let end = self.tail.len() - 2;
            self.hash.update(&self.tail[..end]);
            self.tail.drain(..end);
        }
    }

    fn matches(&self, expected: &str) -> bool {
        let mut lengths = vec![self.tail.len()];
        if self.tail.ends_with(b"\n") {
            lengths.push(self.tail.len() - 1);
        }
        if self.tail.ends_with(b"\r\n") {
            lengths.push(self.tail.len() - 2);
        }
        lengths.into_iter().any(|length| {
            let mut hash = self.hash.clone();
            hash.update(&self.tail[..length]);
            format!("{:x}", hash.finalize()) == expected
        })
    }
}

fn append_quotes(output: &mut Vec<u8>, hashes: &mut Hashes, mut count: usize) {
    while count > 0 {
        let added = count.min(65536 - output.len());
        output.resize(output.len() + added, b'>');
        count -= added;
        if output.len() == 65536 {
            hashes.write(output);
            output.clear();
        }
    }
}

fn candidate(
    file: &mut File,
    start: u64,
    length: u64,
    prefix: &[u8],
    mode: Quoting,
    expected: &str,
) -> Result<(bool, usize)> {
    file.seek(SeekFrom::Start(start))?;
    let mut input = file.take(length);
    let mut hashes = Hashes::new(prefix);
    let mut buffer = [0; 65536];
    let mut output = Vec::with_capacity(65536);
    let mut at_start = true;
    let mut greater = 0_usize;
    let mut matched = 0_usize;
    let mut ambiguous = 0_usize;
    loop {
        let read = input.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        for &byte in &buffer[..read] {
            if at_start {
                if matched == 0 && byte == b'>' {
                    greater += 1;
                    continue;
                }
                if byte == b"From "[matched] {
                    matched += 1;
                    if matched < 5 {
                        continue;
                    }
                    let remove = match mode {
                        Quoting::Stored => false,
                        Quoting::Mboxrd => greater > 0,
                        Quoting::MboxoAll => greater == 1,
                        Quoting::MboxoMask(mask) => {
                            greater == 1 && ambiguous < 12 && mask & (1 << ambiguous) != 0
                        }
                    };
                    if greater == 1 {
                        ambiguous += 1;
                    }
                    append_quotes(&mut output, &mut hashes, greater - usize::from(remove));
                    output.extend_from_slice(b"From ");
                } else {
                    append_quotes(&mut output, &mut hashes, greater);
                    output.extend_from_slice(&b"From "[..matched]);
                    output.push(byte);
                }
                at_start = false;
            } else {
                output.push(byte);
            }
            if byte == b'\n' {
                at_start = true;
                greater = 0;
                matched = 0;
            }
            if output.len() >= 65536 {
                hashes.write(&output);
                output.clear();
            }
        }
    }
    if at_start {
        append_quotes(&mut output, &mut hashes, greater);
        output.extend_from_slice(&b"From "[..matched]);
    }
    hashes.write(&output);
    Ok((hashes.matches(expected), ambiguous))
}

pub fn verify_record(file: &mut File, offset: u64, length: u64, expected: &str) -> Result<()> {
    ensure!(
        expected.len() == 64
            && expected
                .bytes()
                .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()),
        "invalid raw SHA-256"
    );
    file.seek(SeekFrom::Start(offset))?;
    let mut envelope = Vec::new();
    BufReader::new(file.take(length.min(65536))).read_until(b'\n', &mut envelope)?;
    ensure!(
        envelope.starts_with(b"From ") && envelope.ends_with(b"\n"),
        "invalid MBOX envelope"
    );
    let start = offset
        .checked_add(envelope.len() as u64)
        .ok_or_else(|| anyhow::anyhow!("offset overflow"))?;
    let size = length - envelope.len() as u64;
    for prefix in [&[][..], envelope.as_slice()] {
        let (matched, ambiguous) = candidate(file, start, size, prefix, Quoting::Mboxrd, expected)?;
        if matched {
            return Ok(());
        }
        for mode in [Quoting::Stored, Quoting::MboxoAll] {
            if candidate(file, start, size, prefix, mode, expected)?.0 {
                return Ok(());
            }
        }
        if ambiguous <= 12 {
            for mask in 1..(1_u16 << ambiguous).saturating_sub(1) {
                if candidate(
                    file,
                    start,
                    size,
                    prefix,
                    Quoting::MboxoMask(mask),
                    expected,
                )?
                .0
                {
                    return Ok(());
                }
            }
        }
    }
    anyhow::bail!("MBOX record raw SHA-256 mismatch")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn recover_original_bytes_across_framing_variants() -> Result<()> {
        // requirements.md: mboxrd/legacy quoting, adopted delimiters and final-newline policy.
        let envelope = b"From fixture Thu Jan  1 00:00:00 1970\r\n";
        for (stored, original) in [
            (
                &b"Subject: x\n\n>From a\n>>From b\n"[..],
                &b"Subject: x\n\nFrom a\n>From b\n"[..],
            ),
            (&b">From a\n>From b\n"[..], &b"From a\n>From b\n"[..]),
            (
                &b"Subject: x\r\n\r\nbody\r\n"[..],
                &b"Subject: x\r\n\r\nbody"[..],
            ),
            (&b"\n"[..], &b""[..]),
            (&b"body\n"[..], &b"body"[..]),
            (&b"body\rcontent\n"[..], &b"body\rcontent\n"[..]),
            (&b">Fro"[..], &b">Fro"[..]),
        ] {
            let mut file = tempfile::tempfile()?;
            file.write_all(envelope)?;
            file.write_all(stored)?;
            let length = envelope.len() + stored.len();
            for prefix in [&[][..], &envelope[..]] {
                let digest = format!("{:x}", Sha256::digest([prefix, original].concat()));
                verify_record(&mut file, 0, length as u64, &digest)?;
            }
            assert!(verify_record(&mut file, 0, length as u64, &"0".repeat(64)).is_err());
        }
        Ok(())
    }

    #[test]
    fn large_lines_cross_chunk_boundaries_without_losing_bytes() -> Result<()> {
        // requirements.md: streaming byte preservation also applies to long unbroken bodies.
        let mut original = vec![b'>'; 70000];
        original.extend_from_slice(b"From long quote\r\n");
        original.extend(std::iter::repeat_n(b'x', 150000));
        original.extend_from_slice(b"\nFrom final line");
        let mut file = tempfile::tempfile()?;
        file.write_all(b"From fixture\n>")?;
        file.write_all(&original[..original.len() - 15])?;
        file.write_all(b">From final line\n")?;
        let length = file.stream_position()?;
        verify_record(
            &mut file,
            0,
            length,
            &format!("{:x}", Sha256::digest(&original)),
        )
    }
}
