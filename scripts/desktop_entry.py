# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Dispatch the frozen application without requiring an installed Python runtime.
# Headless diagnostics and CLI commands run before any GUI imports.
# The Rust preview uses this executable as its private archive-service helper.
# Its explicit service flag preserves stdin/stdout for the JSON-line protocol.
# Ordinary launches retain the Python GUI until desktop migration is accepted.

"""Frozen application entry point (also usable through make self-test).

Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""

import multiprocessing
import sys


def main() -> int:
    """Keep headless diagnostics independent of Cocoa and user preferences."""
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--rust-engine":
        sys.argv.pop(1)
        from mailarchiver.rust_engine import main as engine_main
        engine_main()
        return 0
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
