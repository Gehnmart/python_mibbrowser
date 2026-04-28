"""Command-line frontend — proof that the engine is reusable without Qt.

Usage:
    python -m pymibbrowser.cli run SCRIPT [--host HOST] [--port PORT]
                                            [--community COMMUNITY]
                                            [--version {1,2c}]
                                            [--save PATH]
    python -m pymibbrowser.cli modules [--list-available | --enabled]

Imports nothing from PyQt — sanity-checked by tests/test_cli.py. The
script-running flow assembles the same engine ports as the GUI's
infra.script_runner uses, so command semantics are identical.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .engine.model import Agent
from .engine.parser import parse_script
from .engine.runner import ExecutionContext, execute
from .infra import config
from .infra.adapters import (
    FileSink,
    MibTreeResolver,
    MibTreeStore,
    NullSink,
    PrintLogger,
    PysnmpTransport,
    WallClock,
)


def _build_agent(args) -> Agent:
    """Override only the fields the user passed; everything else takes
    from saved settings (so the CLI honours the same default agent the
    GUI does)."""
    base = config.load_settings().current_agent
    base.host = args.host or base.host
    base.port = args.port or base.port
    base.read_community = args.community or base.read_community
    if args.version:
        base.version = args.version
    return base


def _build_store() -> MibTreeStore:
    settings = config.load_settings()
    return MibTreeStore(config.compiled_mibs_dir(),
                         enabled=settings.enabled_mibs or [])


def cmd_run(args) -> int:
    """Run an iReasoning-style script through the engine. Same code path
    as the GUI's script_runner — just CLI-shaped logger/sink."""
    script_path = Path(args.script)
    if not script_path.exists():
        print(f"error: script file not found: {script_path}", file=sys.stderr)
        return 2

    agent = _build_agent(args)
    store = _build_store()
    sink = (FileSink(on_persist=lambda p, n: print(f"saved {n} line(s) to {p}"))
            if args.save else NullSink())
    if args.save:
        sink.open(args.save)

    ctx = ExecutionContext(
        agent=agent,
        snmp=PysnmpTransport(),
        clock=WallClock(),
        resolver=MibTreeResolver(store.tree),
        logger=PrintLogger(),
        sink=sink,
    )
    execute(parse_script(script_path.read_text(), default_port=agent.port), ctx)
    return 0


def cmd_modules(args) -> int:
    store = _build_store()
    if args.list_available:
        for m in store.available_modules():
            print(m)
    else:
        for m in store.enabled_modules():
            print(m)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pymibbrowser-cli",
        description="Command-line frontend for the pymibbrowser engine.")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Execute a script file.")
    run.add_argument("script", help="Path to the iReasoning-style script.")
    run.add_argument("--host", help="Override agent host (default: settings).")
    run.add_argument("--port", type=int,
                     help="Override agent port (default: settings).")
    run.add_argument("--community",
                     help="Override read community (default: settings).")
    run.add_argument("--version", choices=("1", "2c"),
                     help="SNMP version (default: settings).")
    run.add_argument("--save", metavar="PATH",
                     help="Write captured output to PATH on completion.")
    run.set_defaults(func=cmd_run)

    mods = sub.add_parser("modules", help="List MIB modules.")
    grp = mods.add_mutually_exclusive_group()
    grp.add_argument("--list-available", action="store_true",
                     help="All modules in the compiled cache.")
    grp.add_argument("--enabled", action="store_true",
                     help="Modules currently merged into the resolver.")
    mods.set_defaults(func=cmd_modules)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
