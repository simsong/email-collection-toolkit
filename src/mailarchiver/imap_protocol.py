# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Verified TLS sessions and bounded read-only IMAP acquisition.

Purpose: Implement verified-TLS, read-only IMAP discovery and byte retrieval.

How it works:
Parse bounded mailbox/FETCH responses and use EXAMINE and BODY.PEEK without changing source
flags.

Fundamental dependents:

- ``imap_source``: remote workers depend on these authenticated sessions and exact body bytes.
"""
from __future__ import annotations

import base64
import imaplib
import re
import ssl
from collections.abc import Sequence
from email.utils import parsedate_to_datetime
from types import TracebackType

from pydantic import BaseModel, Field

from .imap_models import ImapAccount, ImapMailbox, ImapMessageMetadata

IMAP_TIMEOUT = 30
UID_BATCH_SIZE = 256


class ImapToken(BaseModel):
    value: bytes | None = None
    children: list[ImapToken] = Field(default_factory=list)


class ImapBody(BaseModel):
    metadata: bytes
    raw: bytes


def decode_mailbox(value: bytes) -> str:
    """Decode modified UTF-7 mailbox names, accepting native UTF-8 servers too."""
    def decode(match: re.Match[bytes]) -> str:
        encoded = match[1]
        if not encoded:
            return "&"
        return base64.b64decode(encoded.replace(b",", b"/") + b"=" * (-len(encoded) % 4),
                                validate=True).decode("utf-16-be")
    if any(character > 127 for character in value):
        return value.decode("utf-8")
    pieces: list[str] = []
    offset = 0
    for match in re.finditer(rb"&([^-]*)-", value):
        pieces.extend((value[offset:match.start()].decode("ascii"), decode(match)))
        offset = match.end()
    pieces.append(value[offset:].decode("ascii"))
    return "".join(pieces)


def quoted(value: bytes) -> bytes:
    if b"\r" in value or b"\n" in value or b"\x00" in value:
        raise ValueError("IMAP names cannot contain CR, LF or NUL")
    return b'"' + value.replace(b"\\", b"\\\\").replace(b'"', b'\\"') + b'"'


def response_tokens(data: Sequence[bytes | tuple[bytes, bytes] | None]) -> list[ImapToken]:
    """Turn IMAP metadata responses into nested tokens, preserving literal strings."""
    pieces: list[bytes] = []
    for part in data:
        if isinstance(part, tuple):
            prefix, literal = part
            pieces.append(re.sub(rb"\{[0-9]+\}$", b"", prefix) + quoted(literal))
        elif isinstance(part, bytes):
            pieces.append(part)
    raw = b" ".join(pieces)
    roots: list[ImapToken] = []
    stack = [roots]
    offset = 0
    while offset < len(raw):
        character = raw[offset:offset + 1]
        if character.isspace():
            offset += 1
        elif character == b"(":
            token = ImapToken()
            stack[-1].append(token)
            stack.append(token.children)
            offset += 1
        elif character == b")":
            if len(stack) == 1:
                raise ValueError("Unbalanced IMAP metadata")
            stack.pop()
            offset += 1
        elif character == b'"':
            offset += 1
            value = bytearray()
            while offset < len(raw) and raw[offset:offset + 1] != b'"':
                if raw[offset:offset + 1] == b"\\":
                    offset += 1
                if offset >= len(raw):
                    raise ValueError("Invalid quoted IMAP metadata")
                value.append(raw[offset])
                offset += 1
            if offset >= len(raw):
                raise ValueError("Unterminated IMAP string")
            stack[-1].append(ImapToken(value=bytes(value)))
            offset += 1
        else:
            end = offset
            while end < len(raw) and raw[end:end + 1] not in b" \t\r\n()":
                end += 1
            stack[-1].append(ImapToken(value=raw[offset:end]))
            offset = end
    if len(stack) != 1:
        raise ValueError("Unbalanced IMAP metadata")
    return roots


def selected_mailboxes(mailboxes: list[ImapMailbox], gmail: bool) -> list[ImapMailbox]:
    if gmail:
        selected = [mailbox for mailbox in mailboxes if "\\all" in mailbox.flags]
        if len(selected) != 1:
            raise ValueError("Gmail must expose exactly one selectable All Mail (\\All) mailbox")
        return selected
    return [mailbox for mailbox in mailboxes if "spam" not in mailbox.name.casefold()]


def message_metadata(data: Sequence[bytes | tuple[bytes, bytes] | None], mailbox: ImapMailbox,
                     uids: list[int], gmail: bool) -> list[ImapMessageMetadata]:
    tokens = response_tokens(data)
    messages: list[ImapMessageMetadata] = []
    for sequence, fields_token in zip(tokens[::2], tokens[1::2], strict=True):
        if sequence.value is None or not sequence.value.isdigit():
            raise ValueError("Invalid IMAP FETCH sequence")
        fields_list = fields_token.children
        uid: int | None = None
        gmail_id: str | None = None
        labels: tuple[str, ...] = ()
        internal_date = None
        for key, value in zip(fields_list[::2], fields_list[1::2], strict=True):
            name = (key.value or b"").upper()
            if name == b"UID":
                uid = int(value.value or b"0")
            elif name == b"X-GM-MSGID":
                gmail_id = (value.value or b"").decode("ascii")
            elif name == b"X-GM-LABELS":
                labels = tuple(decode_mailbox(item.value or b"") for item in value.children)
            elif name == b"INTERNALDATE":
                internal_date = parsedate_to_datetime((value.value or b"").replace(b"-", b" ", 2).decode("ascii"))
        # A concurrent flag update can arrive without the requested attributes.
        # Requested metadata is complete only when its INTERNALDATE is present.
        if uid not in uids or internal_date is None:
            continue
        if uid is None or (gmail and gmail_id is None):
            raise ValueError("IMAP FETCH omitted a required message identity")
        messages.append(ImapMessageMetadata(uid_validity=mailbox.uid_validity, uid=uid,
            mailbox=mailbox.name, gmail_id=gmail_id, labels=labels, internal_date=internal_date))
    if {message.uid for message in messages} != set(uids) or len(messages) != len(uids):
        raise RuntimeError("IMAP message inventory changed during metadata fetch; retry synchronization")
    return messages


def body_literal(data: Sequence[bytes | tuple[bytes, bytes] | None], uid: int) -> bytes:
    """Verify a complete FETCH envelope without parsing or quoting its body bytes."""
    literals: list[ImapBody] = []
    stripped: list[bytes | tuple[bytes, bytes] | None] = []
    for part in data:
        if isinstance(part, tuple):
            prefix, raw = part
            literals.append(ImapBody(metadata=prefix, raw=raw))
            stripped.append((prefix, b""))
        else:
            stripped.append(part)
    if len(literals) != 1:
        raise RuntimeError("IMAP body disappeared or had an unexpected identity; retry synchronization")
    matches = 0
    tokens = response_tokens(stripped)
    for sequence, fields in zip(tokens[::2], tokens[1::2], strict=True):
        if sequence.value is None or not sequence.value.isdigit():
            raise ValueError("Invalid IMAP FETCH sequence")
        fetched_uid: int | None = None
        size: int | None = None
        has_body = False
        for key, value in zip(fields.children[::2], fields.children[1::2], strict=True):
            name = (key.value or b"").upper()
            if name == b"UID":
                fetched_uid = int(value.value or b"0")
            elif name == b"RFC822.SIZE":
                size = int(value.value or b"-1")
            elif name == b"BODY[]":
                has_body = value.value == b""
        if fetched_uid == uid and has_body:
            if size != len(literals[0].raw):
                raise RuntimeError("IMAP returned an incomplete message body")
            matches += 1
    if matches != 1:
        raise RuntimeError("IMAP body had an unexpected identity or FETCH envelope")
    return literals[0].raw


class ImapSession:
    def __init__(self, account: ImapAccount, password: str,
                 context: ssl.SSLContext | None = None) -> None:
        self.account = account
        self.password = password
        self.context = context or ssl.create_default_context()
        self.client: imaplib.IMAP4 | None = None
        self.gmail = False

    def __enter__(self) -> ImapSession:
        if not self.context.check_hostname or self.context.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError("Verified TLS is required for IMAP")
        try:
            if any(character in self.password for character in "\r\n\x00"):
                raise ValueError("IMAP passwords cannot contain CR, LF or NUL")
            if self.account.tls == "implicit":
                client = imaplib.IMAP4_SSL(self.account.server, self.account.port,
                                           ssl_context=self.context, timeout=IMAP_TIMEOUT)
            else:
                client = imaplib.IMAP4(self.account.server, self.account.port, timeout=IMAP_TIMEOUT)
            self.client = client
            if self.account.tls == "starttls":
                client.starttls(ssl_context=self.context)
            try:
                if b"AUTH=PLAIN" in client.capabilities:
                    credentials = b"\x00" + self.account.username.encode("utf-8") + b"\x00" + self.password.encode("utf-8")
                    client.authenticate("PLAIN", lambda _challenge: credentials)
                else:
                    if not self.account.username.isascii() or not self.password.isascii():
                        raise ValueError("Non-ASCII credentials require a server offering SASL PLAIN")
                    client.login(quoted(self.account.username.encode("ascii")).decode("ascii"), self.password)
            except imaplib.IMAP4.error:
                raise ValueError("IMAP authentication failed; check the password or app password") from None
            status, capabilities = client.capability()
            self._ok(status, "CAPABILITY")
            self.gmail = b"X-GM-EXT-1" in b" ".join(item for item in capabilities if item).upper().split()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise
        finally:
            self.password = ""

    def __exit__(self, _kind: type[BaseException] | None, _error: BaseException | None,
                 _traceback: TracebackType | None) -> None:
        client, self.client = self.client, None
        if client is not None:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                client.shutdown()

    @staticmethod
    def _ok(status: str, command: str) -> None:
        if status != "OK":
            raise RuntimeError(f"IMAP {command} failed")

    def _client(self) -> imaplib.IMAP4:
        if self.client is None:
            raise RuntimeError("IMAP session is not connected")
        return self.client

    def mailboxes(self) -> list[ImapMailbox]:
        status, data = self._client().list('""', '"*"')
        self._ok(status, "LIST")
        tokens = response_tokens(data)
        if len(tokens) % 3:
            raise ValueError("Invalid IMAP mailbox listing")
        mailboxes: list[ImapMailbox] = []
        for offset in range(0, len(tokens), 3):
            flags, delimiter, name = tokens[offset:offset + 3]
            if name.value is None or delimiter.value is None:
                raise ValueError("Invalid IMAP mailbox name")
            attributes = tuple((item.value or b"").decode("ascii").casefold() for item in flags.children)
            if "\\noselect" in attributes:
                continue
            mailboxes.append(ImapMailbox(name=decode_mailbox(name.value),
                wire_name=name.value.decode("utf-8"), flags=attributes,
                delimiter=None if delimiter.value.upper() == b"NIL" else decode_mailbox(delimiter.value)))
        return selected_mailboxes(mailboxes, self.gmail)

    def select(self, mailbox: ImapMailbox) -> ImapMailbox:
        client = self._client()
        status, _data = client.select(quoted(mailbox.wire_name.encode("utf-8")).decode("utf-8"), readonly=True)
        self._ok(status, "EXAMINE")
        validity = client.response("UIDVALIDITY")[1]
        next_uid = client.response("UIDNEXT")[1]
        if not validity or validity[0] is None or not next_uid or next_uid[0] is None:
            raise ValueError("IMAP mailbox did not provide UIDVALIDITY and UIDNEXT")
        return mailbox.model_copy(update={"uid_validity": int(validity[0]), "uid_next": int(next_uid[0])})

    def uids(self, start: int, end: int) -> list[int]:
        client = self._client()
        status, data = client.uid("SEARCH", "UID", f"{start}:{end}")
        self._ok(status, "UID SEARCH")
        uids = [int(uid) for item in data if isinstance(item, bytes) for uid in item.split()]
        # Servers interpret reversed/star ranges specially. Never accept an out-of-range UID.
        if any(uid < start or uid > end for uid in uids):
            raise ValueError("IMAP returned a UID outside the requested snapshot range")
        return uids

    def metadata(self, mailbox: ImapMailbox, uids: list[int]) -> list[ImapMessageMetadata]:
        client = self._client()
        if not uids:
            return []
        fields = "(UID INTERNALDATE X-GM-MSGID X-GM-LABELS)" if self.gmail else "(UID INTERNALDATE)"
        status, data = client.uid("FETCH", ",".join(str(uid) for uid in uids), fields)
        self._ok(status, "UID FETCH metadata")
        messages = message_metadata(data, mailbox, uids, self.gmail)
        self.check_epoch(mailbox.uid_validity)
        return messages

    def check_epoch(self, expected: int) -> None:
        values = self._client().response("UIDVALIDITY")[1]
        if values and any(int(value) != expected for value in values if value is not None):
            raise RuntimeError("IMAP UIDVALIDITY changed during download; retry synchronization")

    def body(self, message: ImapMessageMetadata) -> bytes:
        status, data = self._client().uid("FETCH", str(message.uid), "(UID RFC822.SIZE BODY.PEEK[])")
        self._ok(status, "UID FETCH body")
        self.check_epoch(message.uid_validity)
        return body_literal(data, message.uid)
