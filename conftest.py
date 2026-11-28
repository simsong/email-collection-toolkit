# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Keep ordinary scanner tests independent of definition age and public networks.
# Select the existing definition generation once for this pytest session.
# Pin its directory through the same explicit override used by development tools.
# Subprocess-based ingestion and Quit probes inherit that immutable selection.
# Automatic-refresh tests explicitly remove the pin and supply controlled resources.
# Restore the caller's environment after the session; never download test definitions.
# Standalone converter environments have no application scanner and need no pin.

from collections.abc import Iterator
from importlib.util import find_spec
import os

import pytest

@pytest.fixture(scope="session", autouse=True)
def pinned_scanner_definitions() -> Iterator[None]:
    if find_spec("mailarchiver") is None:
        yield
        return
    from mailarchiver.clamav_definitions import DATABASE_ENV, selected_definitions
    with pytest.MonkeyPatch.context() as patch:
        if not os.environ.get(DATABASE_ENV):
            try:
                definitions = selected_definitions()
            except (OSError, ValueError, OverflowError):
                # Missing-resource tests must remain runnable without installing definitions.
                pass
            else:
                patch.setenv(DATABASE_ENV, str(definitions.directory))
        yield
