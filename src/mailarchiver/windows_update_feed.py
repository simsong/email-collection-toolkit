# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Authenticate complete appcast bytes before exposing updates to WinSparkle.
# Filter the shared feed to the Python package identity and selected channel.
# Preserve signed installer URLs and signatures for the native verifier.
# A token-protected loopback endpoint serves only bounded authenticated XML.
# Source downloads have finite timeouts and never expose local files.
# The application owns the gateway lifetime; it installs no persistent service.
"""Authenticated Python Windows appcast gateway."""
from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
import re
from secrets import token_urlsafe
import socket
from threading import Thread
from time import monotonic
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from typing import Any
import xml.etree.ElementTree as xml

from Cryptodome.Signature import eddsa

from .release_versions import release_metadata
from .updates import UpdateChannel

SPARKLE = "http://www.andymatuschak.org/xml-namespaces/sparkle"
PACKAGE_NAMESPACE = "https://simsong.github.io/email-collection-toolkit/updates"
PACKAGE_IDENTITY = "ECT.PythonReader"
MAX_FEED_BYTES = 16 * 1024 * 1024
SIGNING_BLOCK = re.compile(rb"<!-- sparkle-signatures:\nedSignature: ([A-Za-z0-9+/]{86}==)\nlength: ([0-9]+)\n-->\n?\Z")


class HttpsRedirects(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Request | None:
        if urlsplit(newurl).scheme != "https":
            raise ValueError("Update feed redirected outside HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class BoundedServer(HTTPServer):
    def get_request(self) -> tuple[socket.socket, Any]:
        connection, address = super().get_request()
        connection.settimeout(10)
        return connection, address


def filtered_feed(data: bytes, public_key: str, channel: UpdateChannel) -> bytes:
    """Reject altered metadata; never offer the historical Rust MSIX to Python."""
    if len(data) > MAX_FEED_BYTES:
        raise ValueError("Update feed exceeds its size limit")
    block = SIGNING_BLOCK.search(data)
    if block is None or int(block.group(2)) != block.start():
        raise ValueError("Update feed has no valid complete-XML signature")
    content = data[:block.start()]
    try:
        key = eddsa.import_public_key(base64.b64decode(public_key, validate=True))
        eddsa.new(key, "rfc8032").verify(content, base64.b64decode(block.group(1), validate=True))
    except (ValueError, binascii.Error) as error:
        raise ValueError("Update feed signature verification failed") from error
    root = xml.fromstring(content)
    source = root.find("channel")
    if root.tag != "rss" or source is None:
        raise ValueError("Update feed is not an RSS appcast")
    result = xml.Element("rss", {"version": "2.0"})
    destination = xml.SubElement(result, "channel")
    xml.SubElement(destination, "title").text = "Email Collection Toolkit Python updates"
    for item in source.findall("item"):
        enclosure = item.find("enclosure")
        if enclosure is None:
            raise ValueError("Update item has no enclosure")
        if enclosure.get(f"{{{SPARKLE}}}os") != "windows":
            continue
        if enclosure.get(f"{{{PACKAGE_NAMESPACE}}}packageIdentity") != PACKAGE_IDENTITY:
            continue
        if enclosure.get(f"{{{PACKAGE_NAMESPACE}}}architecture") != "x64":
            continue
        tag = item.findtext("guid", "")
        if not tag.startswith("v"):
            raise ValueError("Update has no release identity")
        expected_tag, track, build, _ = release_metadata(tag[1:])
        if (tag != expected_tag or item.findtext(f"{{{SPARKLE}}}version") != str(build)
                or item.findtext(f"{{{SPARKLE}}}channel", "release") != track):
            raise ValueError("Update metadata differs from the shared release mapper")
        if channel == "release" and track == "preview":
            continue
        url = urlsplit(enclosure.get("url", ""))
        prefix = f"/simsong/email-collection-toolkit/releases/download/{tag}/"
        filename = url.path.removeprefix(prefix)
        if (url.scheme != "https" or url.netloc != "github.com" or url.query or url.fragment
                or not url.path.startswith(prefix) or any(char in filename for char in "/\\%")
                or not filename.endswith((".msix", ".msixbundle"))):
            raise ValueError("Update installer URL is not a repository release asset")
        signature = base64.b64decode(enclosure.get(f"{{{SPARKLE}}}edSignature", ""), validate=True)
        if len(signature) != 64 or int(enclosure.get("length", "0")) <= 0:
            raise ValueError("Update installer has invalid signature metadata")
        destination.append(item)
    return xml.tostring(result, encoding="utf-8", xml_declaration=True)


class FeedGateway:
    """One local request at a time with bounded remote I/O."""

    def __init__(self, source_url: str, public_key: str, channel: Callable[[], UpdateChannel],
                 failed: Callable[[str], None]) -> None:
        parsed = urlsplit(source_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("Update feed must use authenticated HTTPS")
        endpoint = "/" + token_urlsafe(32) + "/appcast.xml"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path != endpoint:
                    self.send_error(404)
                    return
                try:
                    deadline = monotonic() + 30
                    with build_opener(HttpsRedirects()).open(source_url, timeout=10) as response:
                        data = bytearray()
                        while len(data) <= MAX_FEED_BYTES:
                            if monotonic() >= deadline:
                                raise TimeoutError("Update feed download deadline exceeded")
                            block = response.read1(min(65536, MAX_FEED_BYTES + 1 - len(data)))
                            if not block:
                                break
                            data.extend(block)
                    payload = filtered_feed(bytes(data), public_key, channel())
                    self.send_response(200)
                    self.send_header("Content-Type", "application/xml")
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(payload)
                except (OSError, ValueError, xml.ParseError) as error:
                    failed(str(error))
                    self.send_error(502, "Authenticated update feed unavailable")

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.server = BoundedServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}{endpoint}"
        self.thread = Thread(target=self.server.serve_forever, name="winsparkle-feed", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=15)
