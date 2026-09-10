"""Public application identity and read-only discovery of existing settings."""

from pathlib import Path

APPLICATION_NAME = "Email Collection Toolkit"
# Preserve access to settings created under the former two-word product name.
LEGACY_DIRECTORY_NAME = " ".join(("Mail", "Archiver"))


def application_data_directory(root: Path) -> Path:
    """Use the current directory, falling back to existing pre-rename settings."""
    current = root / APPLICATION_NAME
    legacy = root / LEGACY_DIRECTORY_NAME
    if legacy.is_dir() and (not current.is_dir() or not any(current.iterdir())):
        return legacy
    return current
