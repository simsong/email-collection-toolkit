// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
//! Independently recover the SHA-256 identity of catalogued MBOX records.
//! Read bounded chunks, never entire messages or attachments into memory.
//! Try reversible mboxrd and the documented bounded legacy mboxo ambiguity.
//! Adopted envelopes and writer-added terminal newlines are hash-selected.
//! This module reads only; it neither parses MIME nor rewrites stored mail.
//! It supplies catalog verification and bounded reader byte recovery.

use anyhow::{ensure, Result};
use sha2::{Digest, Sha256};
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom};

#[derive(Clone, Copy)]
enum Quoting {
    Stored,
    Mboxrd,
    MboxoAll,
    MboxoMixed,
}

// Keep the final two bytes unhashed so LF/CRLF removal needs no second buffer.
#[derive(Clone)]
struct Hashes {
    hash: Sha256,
    tail: [u8; 2],
    tail_len: usize,
    adopted: bool,
    quoted: u16,
}

struct Recovery {
    quoting: Quoting,
    adopted: bool,
    quoted: u16,
    trim: usize,
}

impl Hashes {
    fn new(prefix: &[u8]) -> Self {
        let mut value = Self {
            hash: Sha256::new(),
            tail: [0; 2],
            tail_len: 0,
            adopted: false,
            quoted: 0,
        };
        value.write(prefix);
        value
    }

    fn write(&mut self, bytes: &[u8]) {
        if bytes.len() >= 2 {
            self.hash.update(&self.tail[..self.tail_len]);
            let end = bytes.len() - 2;
            self.hash.update(&bytes[..end]);
            self.tail.copy_from_slice(&bytes[end..]);
            self.tail_len = 2;
        } else if let Some(&byte) = bytes.first() {
            if self.tail_len == 2 {
                self.hash.update(&self.tail[..1]);
                self.tail[0] = self.tail[1];
                self.tail[1] = byte;
            } else {
                self.tail[self.tail_len] = byte;
                self.tail_len += 1;
            }
        }
    }

    fn matched_trim(&self, expected: &[u8; 32]) -> Option<usize> {
        let tail = &self.tail[..self.tail_len];
        let lengths = [
            Some(tail.len()),
            tail.ends_with(b"\n").then(|| tail.len() - 1),
            tail.ends_with(b"\r\n").then(|| tail.len() - 2),
        ];
        lengths.into_iter().flatten().find_map(|length| {
            let mut hash = self.hash.clone();
            hash.update(&self.tail[..length]);
            (hash.finalize().as_slice() == expected).then_some(tail.len() - length)
        })
    }
}

fn write_hashes(hashes: &mut [Hashes], bytes: &[u8]) {
    for hash in hashes {
        hash.write(bytes);
    }
}

fn append_quotes(output: &mut Vec<u8>, hashes: &mut [Hashes], mut count: usize) {
    while count > 0 {
        let added = count.min(65536 - output.len());
        output.resize(output.len() + added, b'>');
        count -= added;
        if output.len() == 65536 {
            write_hashes(hashes, output);
            output.clear();
        }
    }
}

fn candidate(
    file: &mut (impl Read + Seek),
    start: u64,
    length: u64,
    prefixes: &[Hashes],
    mode: Quoting,
    expected: &[u8; 32],
) -> Result<(Option<Recovery>, usize)> {
    file.seek(SeekFrom::Start(start))?;
    let mut input = file.take(length);
    let mut hashes = prefixes.to_vec();
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
                    if greater == 1 {
                        ambiguous += 1;
                    }
                    if matches!(mode, Quoting::MboxoMixed) && greater == 1 {
                        if ambiguous > 12 {
                            return Ok((None, ambiguous));
                        }
                        // Hash the common prefix once, then fork only this quote decision.
                        write_hashes(&mut hashes, &output);
                        output.clear();
                        let mut quoted = hashes.clone();
                        for hash in &mut quoted {
                            hash.quoted |= 1 << (ambiguous - 1);
                        }
                        write_hashes(&mut quoted, b">From ");
                        write_hashes(&mut hashes, b"From ");
                        hashes.extend(quoted);
                    } else {
                        let remove = match mode {
                            Quoting::Stored | Quoting::MboxoMixed => false,
                            Quoting::Mboxrd => greater > 0,
                            Quoting::MboxoAll => greater == 1,
                        };
                        append_quotes(&mut output, &mut hashes, greater - usize::from(remove));
                        output.extend_from_slice(b"From ");
                    }
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
                write_hashes(&mut hashes, &output);
                output.clear();
            }
        }
    }
    if at_start {
        append_quotes(&mut output, &mut hashes, greater);
        output.extend_from_slice(&b"From "[..matched]);
    }
    write_hashes(&mut hashes, &output);
    Ok((
        hashes.iter().find_map(|hash| {
            hash.matched_trim(expected).map(|trim| Recovery {
                quoting: mode,
                adopted: hash.adopted,
                quoted: hash.quoted,
                trim,
            })
        }),
        ambiguous,
    ))
}

fn record_recovery(
    file: &mut (impl Read + Seek),
    offset: u64,
    length: u64,
    expected: &str,
) -> Result<(u64, Recovery)> {
    ensure!(
        expected.len() == 64
            && expected
                .bytes()
                .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()),
        "invalid raw SHA-256"
    );
    let expected = digest_bytes(expected);
    file.seek(SeekFrom::Start(offset))?;
    // Bound reads by the catalogued record, not an arbitrary envelope length.
    let (envelope_size, mut envelope) = {
        let mut input = BufReader::new(file.take(length));
        let mut prefix = [0; 5];
        input.read_exact(&mut prefix)?;
        ensure!(&prefix == b"From ", "invalid MBOX envelope");
        let mut hashes = Hashes::new(&prefix);
        let mut size = 5_u64;
        loop {
            let bytes = input.fill_buf()?;
            ensure!(!bytes.is_empty(), "unterminated MBOX envelope");
            let end = bytes.iter().position(|&byte| byte == b'\n');
            let consumed = end.map_or(bytes.len(), |index| index + 1);
            hashes.write(&bytes[..consumed]);
            size += consumed as u64;
            input.consume(consumed);
            if end.is_some() {
                break;
            }
        }
        (size, hashes)
    };
    let start = offset
        .checked_add(envelope_size)
        .ok_or_else(|| anyhow::anyhow!("offset overflow"))?;
    let size = length - envelope_size;
    envelope.adopted = true;
    let prefixes = [Hashes::new(&[]), envelope];
    let mut ambiguous = 0;
    for prefix in &prefixes {
        for mode in [Quoting::Mboxrd, Quoting::Stored, Quoting::MboxoAll] {
            let (matched, count) = candidate(
                file,
                start,
                size,
                std::slice::from_ref(prefix),
                mode,
                &expected,
            )?;
            if let Some(recovery) = matched {
                return Ok((envelope_size, recovery));
            }
            ambiguous = count;
        }
    }
    if (2..=12).contains(&ambiguous) {
        if let Some(recovery) =
            candidate(file, start, size, &prefixes, Quoting::MboxoMixed, &expected)?.0
        {
            return Ok((envelope_size, recovery));
        }
    }
    anyhow::bail!("MBOX record raw SHA-256 mismatch")
}

pub fn verify_record(
    file: &mut (impl Read + Seek),
    offset: u64,
    length: u64,
    expected: &str,
) -> Result<()> {
    record_recovery(file, offset, length, expected).map(|_| ())
}

fn digest_bytes(expected: &str) -> [u8; 32] {
    let mut digest = [0; 32];
    for (byte, pair) in digest
        .iter_mut()
        .zip(expected.as_bytes().as_chunks::<2>().0)
    {
        let digit = |v: u8| if v <= b'9' { v - b'0' } else { v - b'a' + 10 };
        *byte = digit(pair[0]) * 16 + digit(pair[1]);
    }
    digest
}

/// Borrowed callers receive independent recovered bytes; owned callers can reuse storage.
pub fn recover_bytes(record: &[u8], expected: &str) -> Result<Vec<u8>> {
    recover_owned(record.to_vec(), expected)
}

/// Compact the hash-selected framing and quoting in the caller's record allocation.
pub fn recover_owned(mut record: Vec<u8>, expected: &str) -> Result<Vec<u8>> {
    let (split, recovery) = record_recovery(
        &mut std::io::Cursor::new(&record),
        0,
        record.len() as u64,
        expected,
    )?;
    let split = usize::try_from(split)?;
    let mut write = if recovery.adopted { split } else { 0 };
    let mut read = split;
    let mut ambiguous = 0;
    while read < record.len() {
        let end = read
            + record[read..]
                .iter()
                .position(|byte| *byte == b'\n')
                .map_or(record.len() - read, |i| i + 1);
        let line = &record[read..end];
        let depth = line.iter().take_while(|byte| **byte == b'>').count();
        let remove = if line[depth..].starts_with(b"From ") {
            match recovery.quoting {
                Quoting::Stored => false,
                Quoting::Mboxrd => depth > 0,
                Quoting::MboxoAll => depth == 1,
                Quoting::MboxoMixed if depth == 1 => {
                    let remove = recovery.quoted & (1 << ambiguous) == 0;
                    ambiguous += 1;
                    remove
                }
                Quoting::MboxoMixed => false,
            }
        } else {
            false
        };
        let begin = read + usize::from(remove);
        record.copy_within(begin..end, write);
        write += end - begin;
        read = end;
    }
    record.truncate(
        write
            .checked_sub(recovery.trim)
            .ok_or_else(|| anyhow::anyhow!("invalid recovered MBOX framing"))?,
    );
    ensure!(
        Sha256::digest(&record).as_slice() == digest_bytes(expected),
        "recovered MBOX bytes do not match raw SHA-256"
    );
    Ok(record)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs::File;
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
                let recovered = [prefix, original].concat();
                let digest = format!("{:x}", Sha256::digest(&recovered));
                verify_record(&mut file, 0, length as u64, &digest)?;
                let record = [envelope.as_slice(), stored].concat();
                let allocation = record.as_ptr();
                let capacity = record.capacity();
                let owned = recover_owned(record, &digest)?;
                assert_eq!(owned, recovered);
                assert_eq!(
                    owned.as_ptr(),
                    allocation,
                    "recovery must reuse the record allocation"
                );
                assert_eq!(owned.capacity(), capacity);
                assert_eq!(
                    recover_bytes(&[envelope.as_slice(), stored].concat(), &digest)?,
                    recovered
                );
            }
            assert!(verify_record(&mut file, 0, length as u64, &"0".repeat(64)).is_err());
            assert!(
                recover_bytes(&[envelope.as_slice(), stored].concat(), &"0".repeat(64)).is_err()
            );
        }
        Ok(())
    }

    #[test]
    fn mixed_recovery_refuses_more_than_twelve_ambiguous_lines() -> Result<()> {
        // Byte recovery must retain the verifier's bounded ambiguity policy.
        let mut record = b"From fixture\n".to_vec();
        let mut original = Vec::new();
        for index in 0..13 {
            record.extend_from_slice(format!(">From {index}\n").as_bytes());
            original.extend_from_slice(
                format!("{}From {index}\n", if index == 12 { ">" } else { "" }).as_bytes(),
            );
        }
        let digest = format!("{:x}", Sha256::digest(original));
        assert!(verify_record(
            &mut std::io::Cursor::new(&record),
            0,
            record.len() as u64,
            &digest
        )
        .is_err());
        assert!(recover_bytes(&record, &digest).is_err());
        Ok(())
    }

    #[test]
    fn mixed_legacy_hashes_share_reads_and_reject_corruption() -> Result<()> {
        // requirements.md: bounded legacy recovery must not reread once per interpretation.
        // Count real file reads; the adapter never substitutes data or I/O behavior.
        struct Measured {
            file: File,
            bytes: u64,
        }
        impl Read for Measured {
            fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
                let count = self.file.read(buffer)?;
                self.bytes += count as u64;
                Ok(count)
            }
        }
        impl Seek for Measured {
            fn seek(&mut self, position: SeekFrom) -> std::io::Result<u64> {
                self.file.seek(position)
            }
        }
        let envelope = b"From fixture\n";
        let mut original = vec![b'x'; 2 * 1024 * 1024];
        original.push(b'\n');
        let mut stored = original.clone();
        for index in 0..12 {
            stored.extend_from_slice(format!(">From line {index}\n").as_bytes());
            // A late legacy mask: decode the first eleven lines, retain the last.
            original.extend_from_slice(
                format!("{}From line {index}\n", if index == 11 { ">" } else { "" }).as_bytes(),
            );
        }
        let mut file = tempfile::tempfile()?;
        file.write_all(envelope)?;
        file.write_all(&stored)?;
        let length = file.stream_position()?;
        let mut measured = Measured { file, bytes: 0 };
        for prefix in [&[][..], envelope.as_slice()] {
            let recovered = [prefix, &original].concat();
            let expected = format!("{:x}", Sha256::digest(&recovered));
            measured.bytes = 0;
            verify_record(&mut measured, 0, length, &expected)?;
            assert!(
                measured.bytes <= 7 * length + 8192,
                "{} bytes reread",
                measured.bytes
            );
            assert_eq!(
                recover_bytes(&[envelope.as_slice(), &stored].concat(), &expected)?,
                recovered
            );
        }
        measured.bytes = 0;
        assert!(verify_record(&mut measured, 0, length, &"0".repeat(64)).is_err());
        assert!(
            measured.bytes <= 7 * length + 8192,
            "{} bytes reread",
            measured.bytes
        );
        Ok(())
    }

    #[test]
    fn long_envelopes_are_streamed_and_confined_to_the_record() -> Result<()> {
        // requirements.md: adopted source bytes remain verifiable without envelope limits.
        let envelope = [b"From ".as_slice(), &vec![b'x'; 200000], b"\r\n"].concat();
        let body = b"Subject: long envelope\n\nbody";
        let mut file = tempfile::tempfile()?;
        file.write_all(b"previous record\n")?;
        let offset = file.stream_position()?;
        file.write_all(&envelope)?;
        file.write_all(body)?;
        file.write_all(b"\n")?;
        let length = file.stream_position()? - offset;
        file.write_all(b"From following\nnext record\n")?;
        for original in [body.to_vec(), [&envelope[..], body].concat()] {
            verify_record(
                &mut file,
                offset,
                length,
                &format!("{:x}", Sha256::digest(original)),
            )?;
        }
        let digest = format!("{:x}", Sha256::digest(body));
        assert!(verify_record(&mut file, offset, 65536, &digest).is_err());
        assert!(verify_record(&mut file, offset, length, &"0".repeat(64)).is_err());
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
