# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Native macOS Keychain and Windows Credential Manager access.

Purpose: Store and retrieve IMAP secrets through native operating-system vaults.

How it works:
Select the native vault explicitly and key secrets by saved account identity.
The named macOS development app reads through its original trusted interpreter;
passwords cross only a captured pipe, never files, arguments or environment.

Fundamental dependents:

- ``imap_cli``: interactive setup must persist passwords outside archives.
- ``imap_source``: download sessions need the matching native-vault credential.
"""
import sys
import re
from pathlib import Path
from importlib import import_module

from keyring.backend import KeyringBackend
from keyring.errors import KeyringError
from pydantic import BaseModel, ConfigDict, Field

from .identity import DEVELOPMENT_BUNDLE_IDENTIFIER
from .imap_models import ImapAccount
from .owned_command import run_owned_command

PASSWORD_KEY = "password"


class NativePassword(BaseModel):
    """Private response from keyring's password-only JSON CLI; never render this value."""
    model_config = ConfigDict(extra="forbid")
    password: str = Field(alias=PASSWORD_KEY, repr=False)


def _credential_error(status: int | None) -> RuntimeError:
    detail = f" (native status {status})" if status is not None else ""
    if status in (-67030, -67050, -25293):
        return RuntimeError(f"macOS rejected access to the saved IMAP password{detail}. "
                            "The application identity may have changed; restart the updated application.")
    return RuntimeError(f"The OS credential store could not read the saved IMAP password{detail}")


def _native_status(error: BaseException) -> int | None:
    seen: set[int] = set()
    cause: BaseException | None = error
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        for value in cause.args:
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        cause = cause.__cause__
    return None


def _development_python() -> str | None:
    """Only the development bundle retains a separate, existing Python Keychain identity."""
    if sys.platform != "darwin":
        return None
    bundle = import_module("Foundation").NSBundle.mainBundle()
    executable = bundle.executablePath()
    if (bundle.bundleIdentifier() == DEVELOPMENT_BUNDLE_IDENTIFIER and executable
            and Path(executable).resolve() != Path(sys.executable).resolve()):
        return sys.executable
    return None


def _development_password(python: str, account: ImapAccount) -> str | None:
    """Reuse existing native-vault trust without granting access or persisting a secret."""
    result = None
    try:
        result = run_owned_command([python, "-I", "-m", "keyring", "--keyring-backend", "keyring.backends.macOS.Keyring",
                                 "--output", "json", "--", "get", account.credential_ref, account.username],
                                   timeout=15)
    except (OSError, RuntimeError, EOFError):
        pass
    if result is None:
        raise RuntimeError("The native credential reader could not finish")
    if result.returncode == 1 and not result.stderr and not result.stdout:
        return None
    if result.returncode:
        codes = re.findall(r"\((-?\d+),", result.stderr)
        raise _credential_error(int(codes[-1]) if codes else None)
    response = None
    try:
        response = NativePassword.model_validate_json(result.stdout)
    except ValueError:
        pass
    # Raise outside the handler: validation errors can contain the private input.
    if response is None:
        raise RuntimeError("The native credential reader returned an invalid response")
    return response.password


def native_keyring() -> KeyringBackend:
    if sys.platform == "darwin":
        from keyring.backends.macOS import Keyring  # pylint: disable=import-outside-toplevel
        return Keyring()
    if sys.platform == "win32":
        from keyring.backends.Windows import WinVaultKeyring  # pylint: disable=import-outside-toplevel
        return WinVaultKeyring()
    raise RuntimeError("IMAP credential storage supports macOS and Windows only")


def password_for(account: ImapAccount) -> str:
    python = _development_python()
    status = None
    failed = False
    try:
        password = _development_password(python, account) if python else native_keyring().get_password(account.credential_ref, account.username)
    except KeyringError as error:
        status, failed = _native_status(error), True
        password = None
    if failed:
        raise _credential_error(status)
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
