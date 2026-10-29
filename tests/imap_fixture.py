# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Run a real disposable pymap server for read-only importer integration tests.
# Each test gets isolated in-memory mailboxes and a temporary TLS certificate.
# Both implicit TLS and STARTTLS use certificate and hostname verification.
# Test setup may populate this synthetic source; the importer must leave it unchanged.
# A tiny injected keyring replaces the OS boundary to avoid changing user credentials.
# All listener tasks and sockets are closed when the fixture leaves its context.
# A minimal TLS protocol fixture covers UTF8=ACCEPT, which pymap does not implement.
"""Isolated real IMAP server and synthetic credential boundary for tests."""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import Future
import imaplib
from pathlib import Path
import shutil
import ssl
import subprocess
from threading import Event, Thread
from typing import Literal

from keyring.backend import KeyringBackend
from jaraco.classes.properties import classproperty
from proxyprotocol.reader import ProxyProtocolReader
from pymap.backend.dict import DictBackend
from pymap.imap import IMAPServer
from pydantic import BaseModel

from mailarchiver.imap_models import ImapAccount


class Secret(BaseModel):
    service: str
    username: str
    password: str


class FixtureKeyring(KeyringBackend):
    """Only the OS secrets boundary is substituted; tests must not edit the user's keychain."""
    @classproperty
    @classmethod
    def priority(cls) -> float:
        return 1.0

    def __init__(self) -> None:
        self.secrets: list[Secret] = []

    def get_password(self, service: str, username: str) -> str | None:
        return next((item.password for item in self.secrets
                     if item.service == service and item.username == username), None)

    def set_password(self, service: str, username: str, password: str) -> None:
        self.secrets = [item for item in self.secrets if (item.service, item.username) != (service, username)]
        self.secrets.append(Secret(service=service, username=username, password=password))


class DisposableImap:
    def __init__(self, directory: Path, tls: Literal["implicit", "starttls"]) -> None:
        self.tls: Literal["implicit", "starttls"] = tls
        self.certificate = directory / "server.crt"
        self.key = directory / "server.key"
        executable = shutil.which("openssl")
        if executable is None:
            raise RuntimeError("openssl is required for disposable IMAP TLS certificates")
        subprocess.run([executable, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-keyout", str(self.key), "-out", str(self.certificate), "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"], check=True, capture_output=True)
        self.context = ssl.create_default_context(cafile=str(self.certificate))
        self.port: Future[int] = Future()
        self.stop = Event()
        self.thread = Thread(target=self._thread, name="disposable-imap", daemon=True)
        self.account: ImapAccount | None = None

    def _thread(self) -> None:
        try:
            asyncio.run(self._serve())
        except BaseException as error:
            if not self.port.done():
                self.port.set_exception(error)
            else:
                raise

    async def _serve(self) -> None:
        args = argparse.Namespace(host="127.0.0.1", port=0, debug=False, cert=str(self.certificate),
            key=str(self.key), tls=True, demo_data=False, demo_user="demouser", demo_password="demopass",
            passlib_cfg=None, proxy_protocol="noop")
        backend, config = await DictBackend.init(args)
        config.apply_context()
        callback = ProxyProtocolReader(config.proxy_protocol).get_callback(IMAPServer(backend.login, config))
        server_context = config.ssl_context if self.tls == "implicit" else None
        server = await asyncio.start_server(callback, host="127.0.0.1", port=0, ssl=server_context)
        async with server:
            self.port.set_result(server.sockets[0].getsockname()[1])
            await asyncio.to_thread(self.stop.wait)

    def start(self) -> ImapAccount:
        self.thread.start()
        self.account = ImapAccount(server="localhost", port=self.port.result(timeout=10),
                                   username="demouser", tls=self.tls)
        return self.account

    def connect(self) -> imaplib.IMAP4:
        if self.account is None:
            raise RuntimeError("fixture server has not started")
        if self.tls == "implicit":
            client = imaplib.IMAP4_SSL("localhost", self.account.port, ssl_context=self.context, timeout=10)
        else:
            client = imaplib.IMAP4("localhost", self.account.port, timeout=10)
            client.starttls(ssl_context=self.context)
        client.login("demouser", "demopass")
        return client

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise RuntimeError("disposable IMAP server did not stop")


class Utf8Imap(DisposableImap):
    """Minimal TLS protocol fixture for RFC 6855, which pymap does not implement.

    Require ENABLE before UTF-8 LIST/EXAMINE and record actual client wire bytes.
    All names, credentials and messages are synthetic; no application methods are mocked.
    """
    def __init__(self, directory: Path, capability: str = "UTF8=ACCEPT", enable: str = "OK") -> None:
        super().__init__(directory, "implicit")
        self.capability = capability.encode("ascii")
        self.enable = enable
        self.commands: list[bytes] = []
        self.names = ("旅行", "literal &AMk- &-", 'Quote " and \\ box')

    async def _serve(self) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.certificate, self.key)
        server = await asyncio.start_server(self._connection, "127.0.0.1", 0, ssl=context)
        async with server:
            self.port.set_result(server.sockets[0].getsockname()[1])
            await asyncio.to_thread(self.stop.wait)

    async def _connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        authenticated = enabled = False
        writer.write(b"* OK Disposable UTF-8 IMAP\r\n")
        await writer.drain()
        try:
            while line := await reader.readline():
                self.commands.append(line)
                tag, command, *arguments = line.rstrip(b"\r\n").split(b" ", 2)
                response = b"OK complete"
                if command == b"CAPABILITY":
                    capabilities = b"IMAP4rev1"
                    if authenticated:
                        capabilities += b" ENABLE " + self.capability
                    writer.write(b"* CAPABILITY " + capabilities + b"\r\n")
                elif command == b"LOGIN":
                    authenticated = True
                elif command == b"ENABLE" and authenticated and arguments == [b"UTF8=ACCEPT"]:
                    if self.enable == "OK":
                        enabled = True
                        writer.write(b"* ENABLED UTF8=ACCEPT\r\n")
                    elif self.enable == "NO":
                        response = b"NO unsupported"
                elif command == b"LIST" and enabled:
                    for name in self.names:
                        escaped = name.encode().replace(b"\\", b"\\\\").replace(b'"', b'\\"')
                        writer.write(b'* LIST () "/" "' + escaped + b'"\r\n')
                elif command == b"EXAMINE" and enabled:
                    writer.write(b"* 1 EXISTS\r\n* OK [UIDVALIDITY 42] valid\r\n* OK [UIDNEXT 2] next\r\n")
                elif command == b"UID" and enabled and arguments == [b"SEARCH UID 1:1"]:
                    writer.write(b"* SEARCH 1\r\n")
                elif command == b"LOGOUT":
                    writer.write(b"* BYE closing\r\n" + tag + b" OK logout\r\n")
                    await writer.drain()
                    break
                else:
                    response = b"BAD unexpected command"
                writer.write(tag + b" " + response + b"\r\n")
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
