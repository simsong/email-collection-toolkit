# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Headless CLI for the empty processor framework and executable test plugins."""
from __future__ import annotations

import argparse
from pathlib import Path
from uuid import uuid4

from .registry import fingerprint, load_processors
from .runtime import run
from .store import connect, report, reprocess, submit
from ..writer_lock import WriterLease


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installation-config", type=Path, help="installation YAML settings path")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--plugin-dir", type=Path, action="append", default=[])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("plugins")
    commands.add_parser("status")
    commands.add_parser("reprocess")
    add = commands.add_parser("submit")
    add.add_argument("source", type=Path)
    execute = commands.add_parser("run")
    execute.add_argument("--retry", action="store_true")
    execute.add_argument("--max-jobs", type=int)
    args = parser.parse_args()
    plugins = load_processors(tuple(args.plugin_dir))
    if args.command == "plugins":
        for plugin in plugins:
            print(plugin.manifest.model_dump_json())
        return
    archive = args.archive.resolve()
    if args.command == "init":
        archive.mkdir(parents=True, exist_ok=True)
    with WriterLease.acquire(archive, str(archive), "processing", str(uuid4()), "2", create=args.command == "init"):
        database = connect(archive, create=args.command == "init")
        try:
            if args.command == "submit":
                print(submit(database, archive, args.source, fingerprint(plugins)))
            elif args.command == "reprocess":
                reprocess(database, archive, fingerprint(plugins))
            elif args.command == "run":
                if args.max_jobs is not None and args.max_jobs < 1:
                    parser.error("--max-jobs must be positive")
                try:
                    result = run(database, plugins, retry=args.retry, max_jobs=args.max_jobs,
                                 installation_config=args.installation_config)
                except KeyboardInterrupt:
                    print(report(database, tuple(p.manifest.kind for p in plugins)).model_dump_json())
                    raise SystemExit(130) from None
                print(result.model_dump_json())
                if result.failed:
                    raise SystemExit(1)
            elif args.command == "status":
                print(report(database, tuple(p.manifest.kind for p in plugins)).model_dump_json())
        finally:
            database.close()


if __name__ == "__main__":
    main()
