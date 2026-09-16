# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Open the disposable matcher UI without loading an archive or preferences."""

import argparse
from pathlib import Path

import webview

from mailarchiver.loopback import LoopbackAssetServer


def main() -> None:
    """Use the application's asset transport; close it with the prototype window."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("name", "institution", "both"), default="both")
    args = parser.parse_args()
    server = LoopbackAssetServer(Path(__file__).resolve().parents[1] / "gui")
    try:
        kinds = ("name", "institution") if args.kind == "both" else (args.kind,)
        for index, kind in enumerate(kinds):
            webview.create_window(
                f"{kind.title()} matcher — Synthetic prototype",
                server.url("matcher.html", [("kind", kind)]),
                width=1120,
                height=790,
                x=70 + index * 60,
                y=70 + index * 60,
                min_size=(780, 600),
            )
        webview.start()
    finally:
        server.close()


if __name__ == "__main__":
    main()
