# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Native macOS Keychain and Windows Credential Manager access.

Purpose: Store and retrieve IMAP secrets through native operating-system vaults.

How it works:
Select macOS Keychain or Windows Credential Manager explicitly and key secrets by saved
account identity.

Fundamental dependents:

- ``imap_cli``: interactive setup must persist passwords outside archives.
- ``imap_source``: download sessions need the matching native-vault credential.
"""
import sys

from keyring.backend import KeyringBackend
from keyring.errors import KeyringError

from .imap_models import ImapAccount


def native_keyring() -> KeyringBackend:
    if sys.platform == "darwin":
        from keyring.backends.macOS import Keyring  # pylint: disable=import-outside-toplevel
        return Keyring()
    if sys.platform == "win32":
        from keyring.backends.Windows import WinVaultKeyring  # pylint: disable=import-outside-toplevel
        return WinVaultKeyring()
    raise RuntimeError("IMAP credential storage supports macOS and Windows only")


def password_for(account: ImapAccount) -> str:
    try:
        password = native_keyring().get_password(account.credential_ref, account.username)
    except KeyringError:
        raise RuntimeError("The OS credential store is unavailable or locked") from None
    if password is None:
        raise ValueError(f"No saved password for IMAP source {account.id}; use --configure")
    return password


def save_password(account: ImapAccount, password: str) -> None:
    if not password:
        raise ValueError("An IMAP password or app password is required")
    try:
        native_keyring().set_password(account.credential_ref, account.username, password)
    except KeyringError:
        raise RuntimeError("Unable to save the password in the OS credential store") from None
