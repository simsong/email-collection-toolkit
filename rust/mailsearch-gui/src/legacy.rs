// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Recognize historical x-html wrappers without materializing container bodies.
// Transfer decoding feeds a bounded charset decoder and a tiny tag matcher.
// Leading/trailing Unicode whitespace is ignored exactly as for string trim.
// UTF-7 runs retain at most 80 symbols and stream through a UTF-16 decoder.
// BOM sniffing and charset aliases match the mailparse charset dependency.
// This probe describes MIME; rendering still uses the existing full text path.
// A definitive prefix mismatch stops charset work while transfer counting continues.
use base64::{engine::general_purpose::STANDARD_NO_PAD, Engine};
use encoding_rs::{CoderResult, Decoder, Encoding};

#[derive(Default)]
struct Tags {
    prefix: usize,
    invalid: bool,
    tail: [u8; 9],
    whitespace: usize,
    length: usize,
}
impl Tags {
    fn write(&mut self, text: &str) {
        for c in text.chars() {
            if self.invalid {
                return;
            }
            if self.prefix == 0 && c.is_whitespace() {
                continue;
            }
            if self.prefix < 8 {
                self.invalid |= !c.eq_ignore_ascii_case(&(b"<x-html>"[self.prefix] as char));
                self.prefix += 1;
            }
            if c.is_whitespace() {
                self.whitespace = (self.whitespace + 1).min(17);
            } else {
                for _ in 0..self.whitespace.min(9) {
                    self.push(b' ');
                }
                self.length = (self.length + self.whitespace + 1).min(17);
                self.whitespace = 0;
                self.push(if c.is_ascii() { c as u8 } else { 0 });
            }
        }
    }
    fn push(&mut self, b: u8) {
        self.tail.rotate_left(1);
        self.tail[8] = b;
    }
    fn matched(&self) -> bool {
        !self.invalid
            && self.prefix == 8
            && self.length >= 17
            && self.tail.eq_ignore_ascii_case(b"</x-html>")
    }
}
fn decoded(decoder: &mut Decoder, mut bytes: &[u8], last: bool, tags: &mut Tags) {
    loop {
        let mut output = [0; 4096];
        let (result, read, written, _) = decoder.decode_to_utf8(bytes, &mut output, last);
        tags.write(
            std::str::from_utf8(&output[..written]).expect("charset decoder produces UTF-8"),
        );
        bytes = &bytes[read..];
        if tags.invalid || result == CoderResult::InputEmpty {
            break;
        }
    }
}
struct Utf7 {
    decoder: Option<Decoder>,
    symbols: [u8; 80],
    used: usize,
    any: bool,
}
impl Utf7 {
    fn new() -> Self {
        Self {
            decoder: None,
            symbols: [0; 80],
            used: 0,
            any: false,
        }
    }
    fn chunk(&mut self, last: bool, tags: &mut Tags) {
        let mut output = [0; 60];
        let mut length = self.used;
        let count = loop {
            match STANDARD_NO_PAD.decode_slice(&self.symbols[..length], &mut output) {
                Ok(count) => break count,
                Err(_) => {
                    length -= 1;
                }
            }
        };
        decoded(self.decoder.as_mut().unwrap(), &output[..count], last, tags);
        if length != self.used {
            tags.write("\u{fffd}");
        }
        self.used = 0;
    }
    fn write(&mut self, bytes: &[u8], tags: &mut Tags) {
        for &b in bytes {
            if tags.invalid {
                break;
            }
            if self.decoder.is_some() {
                if b.is_ascii_alphanumeric() || matches!(b, b'+' | b'/') {
                    if self.used == 80 {
                        self.chunk(false, tags);
                    }
                    self.symbols[self.used] = b;
                    self.used += 1;
                    self.any = true;
                    continue;
                }
                self.end(tags, b == b'-');
                if b == b'-' {
                    continue;
                }
            }
            if b == b'+' {
                self.decoder = Some(encoding_rs::UTF_16BE.new_decoder_without_bom_handling());
                self.any = false;
            } else if b.is_ascii() {
                tags.write(std::str::from_utf8(&[b]).unwrap());
            } else {
                tags.write("\u{fffd}");
            }
        }
    }
    fn end(&mut self, tags: &mut Tags, literal_plus: bool) {
        if self.decoder.is_some() {
            if self.any {
                self.chunk(true, tags);
            } else {
                tags.write(if literal_plus { "+" } else { "\u{fffd}" });
            }
            self.decoder = None;
        }
    }
}

enum Mode {
    Encoded(Decoder),
    Utf7(Utf7),
    Ascii,
}
impl Mode {
    fn write(&mut self, bytes: &[u8], last: bool, tags: &mut Tags) {
        if tags.invalid {
            return;
        }
        match self {
            Self::Encoded(decoder) => decoded(decoder, bytes, last, tags),
            Self::Utf7(decoder) => {
                decoder.write(bytes, tags);
                if last {
                    if !decoder.any && decoder.decoder.is_some() {
                        tags.write("\u{fffd}");
                        decoder.decoder = None;
                    } else {
                        decoder.end(tags, false);
                    }
                }
            }
            Self::Ascii => {
                for chunk in bytes.chunks(1024) {
                    tags.write(&charset::decode_ascii(chunk));
                    if tags.invalid {
                        break;
                    }
                }
            }
        }
    }
}

pub(crate) fn inspect(body: &[u8], transfer: &str, charset_name: &str) -> (bool, Option<usize>) {
    let mut tags = Tags::default();
    let mut prefix = [0; 3];
    let mut used = 0;
    let mut mode = None;
    let make_mode = |prefix: &[u8]| match charset::Charset::for_label(charset_name.as_bytes()) {
        None => Mode::Ascii,
        Some(charset) => {
            if let Some((encoding, _)) = Encoding::for_bom(prefix) {
                Mode::Encoded(encoding.new_decoder())
            } else if charset == charset::UTF_7 {
                Mode::Utf7(Utf7::new())
            } else {
                // Charset resolves extended aliases and unifies GBK; its only
                // non-UTF-7 canonical name absent from label lookup is replacement.
                let encoding = Encoding::for_label(charset.name().as_bytes())
                    .unwrap_or(encoding_rs::REPLACEMENT);
                Mode::Encoded(encoding.new_decoder())
            }
        }
    };
    let result = mime_transfer::decode(body, transfer, false, |mut bytes| {
        if mode.is_none() {
            let count = bytes.len().min(3 - used);
            prefix[used..used + count].copy_from_slice(&bytes[..count]);
            used += count;
            bytes = &bytes[count..];
            if used == 3 {
                let mut decoder = make_mode(&prefix);
                decoder.write(&prefix, false, &mut tags);
                mode = Some(decoder);
            }
        }
        if let Some(mode) = &mut mode {
            mode.write(bytes, false, &mut tags);
        }
    });
    // A failed transfer decode falls back to the complete entity including its
    // transfer-encoding header, which cannot begin with the x-html wrapper.
    if result.is_err() {
        return (false, None);
    }
    match mode {
        Some(mut mode) => mode.write(&[], true, &mut tags),
        None => make_mode(&prefix[..used]).write(&prefix[..used], true, &mut tags),
    }
    (tags.matched(), result.ok())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn check(body: &[u8], charset_name: &str) {
        // requirements.md: bounded legacy probes preserve charset/Unicode trim behavior.
        let expected = charset::Charset::for_label(charset_name.as_bytes())
            .map(|c| c.decode(body).0)
            .unwrap_or_else(|| charset::decode_ascii(body));
        let expected = crate::mime::legacy_html(&expected).is_some();
        for transfer in ["binary", "base64", "quoted-printable"] {
            let encoded = match transfer {
                "base64" => base64::engine::general_purpose::STANDARD
                    .encode(body)
                    .into_bytes(),
                "quoted-printable" => body
                    .iter()
                    .flat_map(|b| format!("={b:02X}").into_bytes())
                    .collect(),
                _ => body.to_vec(),
            };
            assert_eq!(
                inspect(&encoded, transfer, charset_name).0,
                expected,
                "{charset_name} {transfer}: {body:?}"
            );
        }
    }
    #[test]
    fn streaming_probe_matches_charset_reference() {
        for charset in [
            "utf-8",
            "windows-1252",
            "utf-16le",
            "utf-16be",
            "utf-7",
            "unknown",
            "GBK",
            "iso-2022-jp",
            "iso-2022-kr",
        ] {
            for body in [
                b"<x-html></x-html>".as_slice(),
                b" <X-HTML>body</x-html> ",
                b"<x-html>+oops</x-html>",
                b"<x-html>+-</x-html>",
                b"<x-html>+A</x-html>",
                b"<x-html>+////</x-html>",
                b"<x-html>+AAAAA</x-html>",
                b"<x-html>+IAA-</x-html>",
                b"<x-html></x-html>+IAA-",
                b"+ADwAeAAtAGgAdABtAGwAPg-body+ADwALwB4AC0AaAB0AG0AbAA+-",
                b"<x-html></x-html>+",
                b"<x-html></x-html>\xff",
                b"<x-html></x-html>\xff\n",
                b"",
            ] {
                check(body, charset);
            }
        }
        for text in [
            "\u{2003}<X-HTML>body</x-html>\u{2003}",
            "<x-html></x-html>",
            "<x-html> ",
            "<x-html>x</x-html> ",
        ] {
            for bom in [false, true] {
                let mut body = if bom { vec![0xff, 0xfe] } else { Vec::new() };
                body.extend(text.encode_utf16().flat_map(u16::to_le_bytes));
                check(&body, "utf-16le");
                if bom {
                    check(&body, "utf-7");
                    check(&body, "windows-1252");
                }
            }
        }
        for length in [76, 80, 84, 160, 164] {
            let body = format!("<x-html>+{}-</x-html>+IAA-", "AAAA".repeat(length / 4));
            check(body.as_bytes(), "utf-7");
        }
        let large = format!(
            "{}<X-HTML>{}</x-html>{}",
            "\u{2003}".repeat(20000),
            "é".repeat(200000),
            "\u{2003}".repeat(20000)
        );
        check(large.as_bytes(), "utf-8");
    }
    #[test]
    fn extended_aliases_inspect_attachment_and_container_roots() {
        // requirements.md: supported charset labels cannot terminate message inspection.
        for charset in [
            "iso8859_1",
            "iso8859_2",
            "iso8859_3",
            "iso8859_4",
            "iso8859_5",
            "iso8859_6",
            "iso8859_7",
            "iso8859_9",
            "iso8859_13",
            "iso8859_15",
            "ms936",
            "ms949",
            "ms950",
            "ms950_hkscs",
            "ms874",
            "euc_jp",
            "euc_kr",
            "euc_cn",
            "koi8_r",
            "koi8_u",
            "x-windows-874",
            "x-windows-949",
            "x-windows-950",
            "tis620",
            "iso2022jp",
            "x-unicode-2-0-utf-7",
            "unicode-1-1-utf-7",
            "csunicode11utf7",
            "utf-7",
            "iso-2022-kr",
        ] {
            check(b" <X-HTML>body</x-html> ", charset);
            for (mime, disposition) in [
                ("application/octet-stream", ""),
                ("text/plain", "Content-Disposition: attachment\r\n"),
            ] {
                let raw =
                    format!("Content-Type: {mime}; charset={charset}\r\n{disposition}\r\nabc");
                let view = crate::mime::describe(raw.as_bytes()).unwrap();
                assert_eq!(view["preferred_part_id"], -1);
                assert_eq!(view["attachments"][0]["byte_length"], 3);
            }
            let raw = format!("Content-Type: multipart/mixed; charset={charset}; boundary=x\r\n\r\npreamble\r\n--x\r\nContent-Type: text/plain\r\n\r\nsibling\r\n--x--\r\n");
            assert_eq!(
                crate::mime::describe(raw.as_bytes()).unwrap()["preferred_part_id"],
                1
            );
        }
    }
}
