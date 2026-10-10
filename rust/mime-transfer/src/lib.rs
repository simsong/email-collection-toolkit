// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Decode transfer encodings without retaining throwaway attachment payloads.
// A callback receives bounded decoded slices; a counter can discard them.
// Strict mode matches importer validation; permissive mode matches mailparse.
// Both codecs emit bounded batches; quoted-printable borrows filtered line views.
// Only callers needing embedded-message bytes collect the decoded output.
// Neither path rewrites canonical input or relaxes the importer's MIME policy.
use base64::{engine::general_purpose::STANDARD, Engine};

pub fn decode(
    body: &[u8],
    encoding: &str,
    strict: bool,
    mut emit: impl FnMut(&[u8]),
) -> Result<usize, &'static str> {
    let mut length = 0;
    let mut output = |bytes: &[u8]| {
        length += bytes.len();
        emit(bytes);
    };
    match encoding {
        "base64" => base64(body, strict, &mut output)?,
        "quoted-printable" => quoted_printable(body, strict, &mut output)?,
        _ => output(body),
    }
    Ok(length)
}

fn base64(body: &[u8], strict: bool, emit: &mut impl FnMut(&[u8])) -> Result<(), &'static str> {
    if strict
        && body
            .split(|b| *b == b'\n')
            .any(|l| l.strip_suffix(b"\r").unwrap_or(l).len() > 76)
    {
        return Err("base64 line exceeds 76 bytes");
    }
    let mut encoded = [0; 16384];
    let mut used = 0;
    let mut padded = false;
    let mut flush = |input: &[u8]| -> Result<(), &'static str> {
        if !input.len().is_multiple_of(4) || (strict && padded) {
            return Err("invalid base64 body");
        }
        let mut decoded = [0; 12288];
        let output = &mut decoded[..input.len() / 4 * 3];
        let count = if strict {
            STANDARD
                .decode_slice(input, output)
                .map_err(|_| "invalid base64 body")?
        } else {
            data_encoding::BASE64_MIME_PERMISSIVE
                .decode_mut(input, output)
                .map_err(|_| "invalid base64 body")?
        };
        emit(&output[..count]);
        padded = input.contains(&b'=');
        Ok(())
    };
    for &byte in body {
        let whitespace = if strict {
            b" \t\r\n".contains(&byte)
        } else {
            byte.is_ascii_whitespace()
        };
        if whitespace {
            continue;
        }
        encoded[used] = byte;
        used += 1;
        if used == encoded.len() {
            flush(&encoded)?;
            used = 0;
        }
    }
    if used != 0 {
        flush(&encoded[..used])?;
    }
    Ok(())
}

fn quoted_printable(
    body: &[u8],
    strict: bool,
    emit: &mut impl FnMut(&[u8]),
) -> Result<(), &'static str> {
    let mut buffer = [0; 4096];
    let mut used = 0;
    quoted_printable_bytes(body, strict, &mut |bytes| {
        for &byte in bytes {
            buffer[used] = byte;
            used += 1;
            if used == buffer.len() {
                emit(&buffer);
                used = 0;
            }
        }
    })?;
    if used != 0 {
        emit(&buffer[..used]);
    }
    Ok(())
}

fn quoted_printable_bytes(
    body: &[u8],
    strict: bool,
    emit: &mut impl FnMut(&[u8]),
) -> Result<(), &'static str> {
    let valid = |b: u8| matches!(b, b'\t' | b'\r' | b'\n' | b' '..=b'~');
    if strict {
        let mut previous = None;
        for &b in body {
            if !valid(b)
                || (previous.is_none() && b == b'\n')
                || ((previous == Some(b'\r')) != (b == b'\n'))
            {
                return Err("invalid quoted-printable body");
            }
            previous = Some(b);
        }
        if previous == Some(b'\r') {
            return Err("invalid quoted-printable body");
        }
    }
    let end = body.iter().rposition(|b| valid(*b)).map_or(0, |i| i + 1);
    let body = &body[..end];
    let mut add_break = None;
    for raw_line in body.split_inclusive(|b| *b == b'\n') {
        let line = raw_line.strip_suffix(b"\n").unwrap_or(raw_line);
        let end = line
            .iter()
            .rposition(|b| valid(*b) && !b.is_ascii_whitespace())
            .map_or(0, |i| i + 1);
        let mut bytes = line[..end].iter().copied().filter(|b| valid(*b));
        if strict && bytes.clone().count() > 76 {
            return Err("invalid quoted-printable body");
        }
        if add_break == Some(true) {
            emit(b"\r\n");
            add_break = Some(false);
        }
        loop {
            let Some(b) = bytes.next() else {
                add_break = Some(true);
                break;
            };
            if b != b'=' {
                emit(&[b]);
                continue;
            }
            let Some(upper) = bytes.next() else {
                break;
            };
            let Some(lower) = bytes.next() else {
                if strict {
                    return Err("invalid quoted-printable body");
                }
                emit(&[b'=', upper]);
                add_break = Some(true);
                break;
            };
            if upper.is_ascii_hexdigit() && lower.is_ascii_hexdigit() {
                if strict && (upper.is_ascii_lowercase() || lower.is_ascii_lowercase()) {
                    return Err("invalid quoted-printable body");
                }
                let digit = |v: u8| (v as char).to_digit(16).unwrap() as u8;
                emit(&[digit(upper) * 16 + digit(lower)]);
            } else if strict {
                return Err("invalid quoted-printable body");
            } else {
                emit(&[b'=', upper, lower]);
            }
        }
    }
    if strict && add_break == Some(false) {
        return Err("invalid quoted-printable body");
    }
    if body.ends_with(b"\n") && add_break == Some(true) {
        emit(b"\r\n");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn check(input: &[u8], encoding: &str, strict: bool) {
        // requirements.md: allocation reduction preserves strict/permissive MIME semantics.
        let reference = match encoding {
            "base64" if strict => STANDARD
                .decode(
                    input
                        .iter()
                        .copied()
                        .filter(|b| !b" \t\r\n".contains(b))
                        .collect::<Vec<_>>(),
                )
                .map_err(|_| ()),
            "base64" => data_encoding::BASE64_MIME_PERMISSIVE
                .decode(
                    &input
                        .iter()
                        .copied()
                        .filter(|b| !b.is_ascii_whitespace())
                        .collect::<Vec<_>>(),
                )
                .map_err(|_| ()),
            _ => quoted_printable::decode(
                input,
                if strict {
                    quoted_printable::ParseMode::Strict
                } else {
                    quoted_printable::ParseMode::Robust
                },
            )
            .map_err(|_| ()),
        };
        let mut output = Vec::new();
        let result = decode(input, encoding, strict, |bytes| {
            output.extend_from_slice(bytes)
        });
        assert_eq!(
            result.is_ok(),
            reference.is_ok(),
            "{encoding} strict={strict} {input:?}"
        );
        if let Ok(reference) = reference {
            assert_eq!(output, reference, "{encoding} strict={strict} {input:?}");
            assert_eq!(result.unwrap(), output.len());
            assert_eq!(
                decode(input, encoding, strict, |_| {}).unwrap(),
                output.len()
            );
        }
    }
    #[test]
    fn transfer_modes_match_reference_decoders() {
        // Exhaust short malformed streams, whitespace, padding and soft line breaks.
        for (encoding, alphabet, maximum) in [
            ("quoted-printable", b"A0af= \t\r\n\xff".as_slice(), 5),
            ("base64", b"AZa0+/= \n\xff".as_slice(), 5),
        ] {
            let mut inputs = vec![Vec::new()];
            for _ in 0..maximum {
                let mut next = Vec::new();
                for prefix in inputs {
                    for &b in alphabet {
                        let mut input = prefix.clone();
                        input.push(b);
                        check(&input, encoding, false);
                        // Strict base64 line limit is a separate importer check.
                        check(&input, encoding, true);
                        next.push(input);
                    }
                }
                inputs = next;
            }
        }
        for length in [16380, 16384, 16388, 32768] {
            let prefix = b"YWJj".repeat(length / 4);
            for suffix in [b"YQ==".as_slice(), b"YQ==Yg==", b"Y", b"\n"] {
                let input = [prefix.as_slice(), suffix].concat();
                check(&input, "base64", false);
                let wrapped = input
                    .chunks(76)
                    .flat_map(|line| line.iter().copied().chain(b"\r\n".iter().copied()))
                    .collect::<Vec<_>>();
                check(&wrapped, "base64", true);
            }
        }
        for input in [
            b"YQ==Yg==".as_slice(),
            b"YQ= =\r\n",
            b"YQ==\x0b",
            b"=\r\n",
            b"a=\r\n",
            b"a\r\n\xff",
            b"a=\r\n\xff",
        ] {
            check(input, "base64", false);
            check(input, "quoted-printable", false);
            check(input, "quoted-printable", true);
        }
    }
    #[test]
    fn large_payloads_emit_only_bounded_slices() {
        let body = b"YWJj\r\n".repeat(300000);
        let count = decode(&body, "base64", true, |bytes| assert!(bytes.len() <= 12288)).unwrap();
        assert_eq!(count, 900000);
        assert!(decode(&[b'A'; 77], "base64", true, |_| {}).is_err());
        assert!(decode(&[b'A'; 77], "quoted-printable", true, |_| {}).is_err());
        assert_eq!(
            decode(&vec![b' '; 2000000], "quoted-printable", false, |_| panic!(
                "trailing whitespace"
            ))
            .unwrap(),
            0
        );
        // requirements.md: bounded QP batches must avoid per-byte consumer calls.
        let plain = b"ordinary = data\r\n".repeat(20000);
        let encoded = quoted_printable::encode(&plain);
        for strict in [false, true] {
            let mut calls = 0;
            let mut output = Vec::new();
            let count = decode(&encoded, "quoted-printable", strict, |bytes| {
                assert!(bytes.len() <= 4096);
                calls += 1;
                output.extend_from_slice(bytes);
            })
            .unwrap();
            assert_eq!(output, plain);
            assert_eq!(count, plain.len());
            assert_eq!(calls, count.div_ceil(4096));
        }
    }
}
