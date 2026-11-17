# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Carry desktop power and route notifications to active IMAP workers.
# Capture a session token before opening a socket and expire it on sleep or wake.
# A worker waiting for wake also listens to its own import cancellation.
# Route changes wake transport retries through the same activity signal.
# Workers retain ownership of their sockets, UID plans and checkpoints.
"""Power and route signals for IMAP transport recovery."""

from concurrent.futures import FIRST_COMPLETED, Future, wait
from threading import Event

from .change_signal import ChangeEvent, ChangeSignal


class ImapTransport:
    def __init__(self) -> None:
        self.changed = ChangeSignal()
        self.path_available: bool | None = None
        self._sleeping = Event()
        self._session = ChangeSignal()

    def sleep(self) -> None:
        self._sleeping.set()
        self._session.notify()

    def wake(self) -> None:
        self._session.notify()
        self._sleeping.clear()
        self.changed.notify()

    def network_changed(self, path_available: bool) -> None:
        self.path_available = path_available
        self.changed.notify()

    def session(self, cancellation: ChangeEvent) -> Future[None] | None:
        """Wait for wake or cancellation without consuming connection attempts."""
        while True:
            changed, stopped = self.changed.ready, cancellation.changed.ready
            session = self._session.ready
            if cancellation.is_set():
                return None
            if not self._sleeping.is_set():
                return session
            wait((changed, stopped), return_when=FIRST_COMPLETED)
