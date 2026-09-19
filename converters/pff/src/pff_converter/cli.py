# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Convert a read-only PST/OST to mboxrd on stdout; diagnostics go to stderr."""
import argparse
import sys
from contextlib import nullcontext
from pathlib import Path

from .reader import PffSettings, convert


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--diagnostics", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=60)
    parser.add_argument("--max-message-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--max-folder-depth", type=int, default=64)
    args = parser.parse_args()
    try:
        source = args.source.resolve(strict=True)
        destinations = [path.resolve() for path in (args.receipt, args.diagnostics) if path]
        if any(path == source or (path.exists() and path.samefile(source)) for path in destinations) or len(set(destinations)) != len(destinations):
            raise ValueError("receipt and diagnostics must be distinct from the source and each other")
        settings = PffSettings(timeout_seconds=args.timeout_seconds,
                               max_message_bytes=args.max_message_bytes,
                               max_folder_depth=args.max_folder_depth)
        with args.diagnostics.open("w") if args.diagnostics else nullcontext(sys.stderr) as diagnostics:
            receipt = convert(source, sys.stdout.buffer, diagnostics, settings)
        if args.receipt:
            args.receipt.write_text(receipt.model_dump_json())
        print(receipt.model_dump_json(), file=sys.stderr)
        # Exit 3 means every emitted record finished, but some source items were incomplete.
        return 0 if receipt.complete else 3 if receipt.errors and receipt.failure == f"libpff extraction incomplete ({receipt.errors} items)" else 1
    except (OSError, ValueError, RuntimeError) as error:
        print(f"pff-converter: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
