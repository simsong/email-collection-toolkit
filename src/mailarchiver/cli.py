# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Provide the installable Email Collection Toolkit command-line interface.
# One dispatcher serves ect and the create/import/search executable shortcuts.
# Commands reuse the existing archive, IMAP, search and verification engines.
# Imports select local sources or saved read-only IMAP accounts explicitly.
# Argument lists are passed directly; dispatch never invokes Make or a shell.
# Archive creation and import retain the shared writer leases and safety checks.
# Developer build and test orchestration remains separate in the Makefile.
"""Create, import, search and maintain local email archives."""

from __future__ import annotations

import argparse
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
import sys

from pydantic import BaseModel

from . import __main__ as engine
from .archive_path import add_archive_argument, require_archive


class Command(BaseModel):
    """A public command and its existing implementation module."""

    name: str
    module: str
    help: str


COMMANDS = (
    Command(name="search", module="mailsearch", help="search messages or read a message number"),
    Command(name="verify", module="standalone_verify", help="verify archive checksums without changing mail"),
    Command(name="gui", module="gui_app", help="open the desktop application"),
    Command(name="imap", module="imap_cli", help="configure and synchronize IMAP accounts"),
    Command(name="update-definitions", module="clamav_update", help="update antivirus definitions"),
    Command(name="compare-apple-mail", module="apple_mail_compare", help="compare an Apple Mail cache read-only"),
    Command(name="extract-pdf-mail", module="pdf_mail", help="extract printed email into a new derived MBOX"),
    Command(name="h3-review", module="h3_review", help="export a private semantic-hash review"),
    Command(name="validation", module="validation", help="manage explicitly selected validation corpora"),
    Command(name="processor", module="processing.__main__", help="run the disposable processor development harness"),
)
ARCHIVE_COMMANDS = ("process", "processing-status", "processors", "review", "report", "refresh-index",
                    "identities", "human-contacts")


def create(arguments: list[str], prog: str) -> int:
    """Create only a new or empty destination using the shared archive lifecycle."""
    from .application import create_empty_archive  # pylint: disable=import-outside-toplevel
    from .owner_rules import OwnerRules  # pylint: disable=import-outside-toplevel

    parser = argparse.ArgumentParser(prog=prog, description="Create a new, empty email archive.")
    parser.add_argument("archive", type=Path, metavar="ARCHIVE")
    parser.add_argument("--owner", action="append", default=[], metavar="ADDRESS",
                        help="archive owner's email address or include rule; repeat for aliases")
    args = parser.parse_args(arguments)
    document = create_empty_archive(args.archive, owner_rules=OwnerRules(include=args.owner) if args.owner else None)
    print(document.path)
    return 0


def import_mail(arguments: list[str], prog: str) -> int:
    """Select the existing local or IMAP import engine without changing source mail."""
    from .application import ArchiveDocument  # pylint: disable=import-outside-toplevel
    from .imap_cli import synchronize  # pylint: disable=import-outside-toplevel

    parser = argparse.ArgumentParser(prog=prog, description="Import local files/directories or read-only IMAP into an existing archive.")
    add_archive_argument(parser, "destination archive directory")
    engine.add_ingest_arguments(parser, require_scan=False, require_roots=False)
    parser.add_argument("--imap", action="store_true", help="download saved IMAP accounts; prompt for the first account")
    parser.add_argument("--source", help="with --imap, select one saved source ID")
    setup = parser.add_mutually_exclusive_group()
    setup.add_argument("--configure", action="store_true", help="with --imap, update a saved account and credentials")
    setup.add_argument("--add-source", action="store_true", help="with --imap, add another account")
    parser.add_argument("--show-messages", action="store_true", help="with --imap, print each completed download")
    args = parser.parse_args(arguments)
    args.archive = require_archive(parser, args.archive)
    if args.imap:
        if (args.roots or args.owner_names_file or args.plugin_dir or args.installation_config
                or args.defer_content or args.index_attachments or args.earliest_year != 1900):
            parser.error("--imap cannot be combined with local source paths or local import options")
        args.new = None
        return synchronize(args)
    if args.source or args.configure or args.add_source or args.show_messages:
        parser.error("IMAP account options require --imap")
    if not args.roots:
        parser.error("supply a source file/directory, or use --imap")
    ArchiveDocument.open(args.archive)
    args.command, args.function = "ingest", engine.ingest
    return engine.execute(args, parser)


def main(argv: list[str] | None = None) -> int:
    """Dispatch both ect COMMAND and the equivalent ect-COMMAND shortcuts."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    executable = Path(sys.argv[0]).stem
    shortcut = executable.removeprefix("ect-") if executable.startswith("ect-") else None
    parser = argparse.ArgumentParser(prog="ect", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Commands:\n  create              create a new archive\n  import              import local sources or IMAP\n" +
        "\n".join(f"  {item.name:<19} {item.help}" for item in COMMANDS) +
        "\n  " + " / ".join(ARCHIVE_COMMANDS) + "\n  summarize           summarize standard input locally")
    parser.add_argument("--version", action="version", version=version("mailarchiver"))
    names = ("create", "import", *(command.name for command in COMMANDS), *ARCHIVE_COMMANDS, "summarize")
    parser.add_argument("command", choices=names, nargs="?", metavar="COMMAND", help="operation; use COMMAND --help for options")
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    if shortcut is None:
        args = parser.parse_args(arguments)
        if args.command is None:
            parser.print_help()
            return 0
        command, arguments, prog = args.command, args.arguments, f"ect {args.command}"
    else:
        command, prog = shortcut, executable
        if command not in ("create", "import", "search"):
            parser.error(f"unknown shortcut: {executable}")
    try:
        if command == "create":
            return create(arguments, prog)
        if command == "import":
            return import_mail(arguments, prog)
        if command in ARCHIVE_COMMANDS:
            return engine.main([command, *arguments], prog="ect")
        if command == "summarize":
            summary = argparse.ArgumentParser(prog=prog, description="Summarize standard input using local Apple Foundation Models.")
            summary.parse_args(arguments)
            from .aisummarize import main as summarize  # pylint: disable=import-outside-toplevel
            return summarize()
        selected = next(item for item in COMMANDS if item.name == command)
        result = import_module(f".{selected.module}", __package__).main(arguments, prog=prog)
        return result or 0
    except KeyboardInterrupt:
        print("Interrupted; completed archive work is retained.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        print(f"{prog}: {engine.safe_status_text(str(error))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
