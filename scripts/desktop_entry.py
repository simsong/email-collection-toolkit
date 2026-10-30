# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Dispatch the frozen application without requiring an installed Python runtime.
# Headless diagnostics and CLI commands run before any GUI imports.
# Installed Windows acceptance and the CLI share this frozen entry point.
# Normal launches activate the supported Python desktop and native event loop.
# The retired experimental UI and its private service are archived in Git.

"""Frozen application entry point (also usable through make self-test).

Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""

import multiprocessing
import sys


def main() -> int:
    """Keep headless diagnostics independent of Cocoa and user preferences."""
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--update-virus-definitions":
        from mailarchiver.clamav_update import main as update_main
        return update_main(sys.argv[2:], require_directory=True)
    if len(sys.argv) > 1 and sys.argv[1] == "--msix-test":
        from mailarchiver.windows_self_test import main as windows_test_main
        return windows_test_main()
    if "--self-test" in sys.argv or "--self-test-gui" in sys.argv:
        from mailarchiver.self_test import main as test_main
        return test_main()
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        sys.argv.pop(1)
        from mailarchiver.__main__ import main as cli_main
        return cli_main()
    from mailarchiver.gui_app import main as gui_main
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
