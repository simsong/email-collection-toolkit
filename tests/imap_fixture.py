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
from collections.abc import Awaitable, Callable
import base64
from concurrent.futures import Future
import imaplib
from pathlib import Path
import shutil
import ssl
import subprocess
from threading import Event, Lock, Thread
from typing import Literal

from keyring.backend import KeyringBackend
from jaraco.classes.properties import classproperty
from proxyprotocol.reader import ProxyProtocolReader
from proxyprotocol.sock import SocketInfo
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


class AcknowledgedReader(asyncio.StreamReader):
    """Retain the real TLS stream while gating complete synthetic body-fetch commands."""

    def __init__(self, reader: asyncio.StreamReader, before: Callable[[bytes], Awaitable[None]]) -> None:
        super().__init__()
        self.source = reader
        self.before = before

    async def readline(self) -> bytes:
        line = await self.source.readline()
        await self.before(line)
        return line

    async def readexactly(self, n: int) -> bytes:
        return await self.source.readexactly(n)


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
        self._gate_lock = Lock()
        self._body_limit: int | None = None
        self._bodies = 0
        self.active_connections = 0
        self.peak_connections = 0
        self.connection_target: int | None = None
        self.connections_ready = Event()
        self.commands: list[bytes] = []
        self.fetch_paused = Event()
        self.fetch_release = Event()

    def pause_after_bodies(self, count: int) -> None:
        """Keep downloads incomplete until the parent acknowledges the stop notice."""
        if count < 1:
            raise ValueError("At least one body must be allowed before pausing")
        with self._gate_lock:
            self._body_limit, self._bodies = count, 0
            self.fetch_paused.clear()
            self.fetch_release.clear()

    def release_bodies(self) -> None:
        with self._gate_lock:
            self._body_limit = None
        self.fetch_release.set()

    async def _before_command(self, line: bytes) -> None:
        self.commands.append(line)
        command = line.upper()
        if b" UID FETCH " not in command or b"BODY.PEEK[]" not in command:
            return
        with self._gate_lock:
            self._bodies += 1
            pause = self._body_limit is not None and self._bodies > self._body_limit
        if pause:
            self.fetch_paused.set()
            await asyncio.to_thread(self.fetch_release.wait)

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
        protocol = IMAPServer(backend.login, config)

        async def connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, info: SocketInfo) -> None:
            self.active_connections += 1
            self.peak_connections = max(self.peak_connections, self.active_connections)
            if self.active_connections == self.connection_target:
                self.connections_ready.set()
            try:
                await protocol(AcknowledgedReader(reader, self._before_command), writer, info)
            finally:
                self.active_connections -= 1

        callback = ProxyProtocolReader(config.proxy_protocol).get_callback(connection)
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
        self.release_bodies()
        self.stop.set()
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise RuntimeError("disposable IMAP server did not stop")


class ImapFault(BaseModel):
    command: bytes
    mode: Literal["timeout", "disconnect", "partial", "malformed", "reject"]
    refusal: bytes = b"NO [ALERT] Too many simultaneous connections."
    skip: int = 0
    response: bytes = b"invalid IMAP response\r\n"


class InterruptibleImap(DisposableImap):
    """Interrupt real TLS requests through a proxy to the disposable pymap backend."""
    def __init__(self, directory: Path) -> None:
        super().__init__(directory, "implicit")
        self.fault: ImapFault | None = None
        self.commands: list[bytes] = []

    async def _serve(self) -> None:
        args = argparse.Namespace(host="127.0.0.1", port=0, debug=False, cert=str(self.certificate),
            key=str(self.key), tls=True, demo_data=False, demo_user="demouser", demo_password="demopass",
            passlib_cfg=None, proxy_protocol="noop")
        backend, config = await DictBackend.init(args)
        config.apply_context()
        callback = ProxyProtocolReader(config.proxy_protocol).get_callback(IMAPServer(backend.login, config))
        upstream = await asyncio.start_server(callback, "127.0.0.1", 0)
        upstream_port = upstream.sockets[0].getsockname()[1]

        async def connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            remote_reader, remote_writer = await asyncio.open_connection("127.0.0.1", upstream_port)

            async def requests() -> None:
                while line := await reader.readline():
                    self.commands.append(line)
                    fault = self.fault
                    if fault is not None and fault.command in line:
                        if fault.skip:
                            fault.skip -= 1
                        else:
                            if fault.mode == "reject":
                                self.fault = None
                                writer.write(line.split()[0] + b" " + fault.refusal + b"\r\n")
                                await writer.drain()
                                return
                            if fault.mode == "partial":
                                writer.write(b"* 2 FETCH (UID 2 RFC822.SIZE 100 BODY[] {100}\r\nSubject: partial\r\n")
                                await writer.drain()
                            elif fault.mode == "malformed":
                                writer.write(fault.response)
                                await writer.drain()
                            if fault.mode != "disconnect":
                                await reader.read()  # Client timeout closes this stalled transport.
                            return
                    remote_writer.write(line)
                    await remote_writer.drain()

            async def responses() -> None:
                while data := await remote_reader.read(65536):
                    writer.write(data)
                    await writer.drain()

            tasks = [asyncio.create_task(requests()), asyncio.create_task(responses())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                remote_writer.close()
                writer.close()
                await asyncio.gather(remote_writer.wait_closed(), writer.wait_closed(), return_exceptions=True)

        async with upstream:
            server = await asyncio.start_server(connection, "127.0.0.1", 0, ssl=config.ssl_context)
            async with server:
                self.port.set_result(server.sockets[0].getsockname()[1])
                await asyncio.to_thread(self.stop.wait)


class Utf8Imap(DisposableImap):
    """Minimal TLS protocol fixture for RFC 6855, which pymap does not implement.

    Require ENABLE before UTF-8 LIST/EXAMINE and record actual client wire bytes.
    All names, credentials and messages are synthetic; no application methods are mocked.
    """
    def __init__(self, directory: Path, capability: str = "UTF8=ACCEPT", enable: str = "OK", *,
                 credentials: tuple[str, str] | None = None, login_response: bytes | None = None,
                 login_failures: int | None = None, greeting: bytes = b"* OK Disposable UTF-8 IMAP\r\n",
                 setup_command: bytes | None = None, setup_response: bytes | None = None,
                 setup_authenticated: bool = True, tls: Literal["implicit", "starttls"] = "implicit") -> None:
        super().__init__(directory, tls)
        self.capability = capability.encode("ascii")
        self.enable = enable
        self.credentials = credentials
        self.login_response = login_response
        self.login_failures = login_failures
        self.greeting = greeting
        self.setup_command = setup_command
        self.setup_response = setup_response
        self.setup_authenticated = setup_authenticated
        self.server_context: ssl.SSLContext | None = None
        self.commands: list[bytes] = []
        self.names = ("旅行", "literal &AMk- &-", 'Quote " and \\ box')

    def _login_refusal(self) -> bytes | None:
        if self.login_failures == 0:
            return None
        if self.login_failures is not None:
            self.login_failures -= 1
        return self.login_response

    async def _serve(self) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.certificate, self.key)
        self.server_context = context
        server = await asyncio.start_server(self._connection, "127.0.0.1", 0,
                                            ssl=context if self.tls == "implicit" else None)
        async with server:
            self.port.set_result(server.sockets[0].getsockname()[1])
            await asyncio.to_thread(self.stop.wait)

    async def _connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        authenticated = enabled = False
        encrypted = self.tls == "implicit"
        writer.write(self.greeting)
        await writer.drain()
        try:
            while line := await reader.readline():
                self.commands.append(line)
                tag, command, *arguments = line.rstrip(b"\r\n").split(b" ", 2)
                response = b"OK complete"
                if (authenticated == self.setup_authenticated and command == self.setup_command
                        and self.setup_response is not None):
                    response, self.setup_response = self.setup_response, None
                elif command == b"CAPABILITY":
                    capabilities = b"IMAP4rev1"
                    if not encrypted:
                        capabilities += b" STARTTLS LOGINDISABLED"
                    elif not authenticated and self.credentials is not None:
                        capabilities += b" AUTH=PLAIN LOGINDISABLED"
                    if authenticated:
                        capabilities += b" ENABLE " + self.capability
                    writer.write(b"* CAPABILITY " + capabilities + b"\r\n")
                elif command == b"STARTTLS" and not encrypted:
                    assert self.server_context is not None
                    writer.write(tag + b" OK Begin TLS\r\n")
                    await writer.drain()
                    await writer.start_tls(self.server_context)
                    encrypted = True
                    continue
                elif command == b"LOGIN":
                    refusal = self._login_refusal()
                    if refusal is not None:
                        response = refusal
                    elif self.credentials is None:
                        authenticated = True
                    else:
                        response = b"NO LOGIN disabled"
                elif command == b"AUTHENTICATE" and arguments == [b"PLAIN"] and self.credentials is not None:
                    writer.write(b"+ \r\n")
                    await writer.drain()
                    credentials = base64.b64decode((await reader.readline()).strip(), validate=True)
                    username, password = self.credentials
                    authenticated = credentials == b"\0" + username.encode() + b"\0" + password.encode()
                    if not authenticated:
                        response = b"NO invalid credentials"
                    refusal = self._login_refusal()
                    if refusal is not None:
                        response, authenticated = refusal, False
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
                if response.startswith(b"BYE "):
                    writer.write(b"* " + response + b"\r\n")
                    await writer.drain()
                    return
                writer.write(tag + b" " + response + b"\r\n")
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
