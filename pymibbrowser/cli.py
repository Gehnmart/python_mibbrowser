"""Command-line frontend — proof that the engine is reusable without Qt.

Usage:
    pymibbrowser-cli run SCRIPT [--host --port --community --version --save]
    pymibbrowser-cli modules [--list-available | --enabled]
    pymibbrowser-cli walk OID [--host --port --community --version]
    pymibbrowser-cli compile-mibs [--source DIR ...] [--use-network]
    pymibbrowser-cli sniff-traps [--port 162] [--accept-from CIDR]

Imports nothing from PyQt — sanity-checked by tests/test_cli.py. Each
subcommand assembles the same engine ports as the GUI uses, so the
behaviour is identical modulo presentation.
"""
from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from .engine.model import Agent, TrapEvent, VarBind
from .engine.parser import parse_script
from .engine.runner import ExecutionContext, execute
from .infra import config
from .infra.adapters import (
    FileSink,
    MibTreeResolver,
    MibTreeStore,
    NullSink,
    PrintLogger,
    PysmiMibCompiler,
    PysnmpTransport,
    UdpTrapSubscription,
    WallClock,
)


def _build_agent(args: argparse.Namespace) -> Agent:
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


def _format_varbind(vb: VarBind) -> str:
    return f".{'.'.join(map(str, vb.oid))}\t{vb.type_name}\t{vb.display_value}"


# --- run -----------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
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


# --- modules -------------------------------------------------------------


def cmd_modules(args: argparse.Namespace) -> int:
    store = _build_store()
    if args.list_available:
        for m in store.available_modules():
            print(m)
    else:
        for m in store.enabled_modules():
            print(m)
    return 0


# --- walk ----------------------------------------------------------------


def cmd_walk(args: argparse.Namespace) -> int:
    """SNMP walk: GETNEXT loop until we leave the requested subtree.
    Stays inside engine territory — the loop is just an inline
    transcription of the engine's _run_get_or_next + boundary check."""
    agent = _build_agent(args)
    store = _build_store()
    resolver = MibTreeResolver(store.tree)
    transport = PysnmpTransport()

    root = resolver.resolve(args.oid)
    if root is None:
        print(f"error: cannot resolve OID: {args.oid}", file=sys.stderr)
        return 2

    current: tuple[int, ...] = root
    seen = 0
    try:
        while True:
            try:
                vbs = transport.get_next(agent, [current])
            except Exception as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            if not vbs:
                break
            vb = vbs[0]
            # Left the subtree — we're done.
            if vb.oid[: len(root)] != root:
                break
            print(_format_varbind(vb))
            seen += 1
            current = vb.oid
    except KeyboardInterrupt:
        print(f"\n[interrupted after {seen} varbinds]", file=sys.stderr)
        return 130
    return 0


# --- compile-mibs --------------------------------------------------------


def cmd_compile_mibs(args: argparse.Namespace) -> int:
    """Compile every MIB found under the given source directories into
    the user's compiled-MIB cache. Reports per-module status; exit
    code 1 if any module failed."""
    sources: list[Path] = ([Path(d) for d in args.source]
                            if args.source else [config.default_mibs_src()])
    compiler = PysmiMibCompiler(config.compiled_mibs_dir())
    modules = compiler.discover(sources)
    if not modules:
        print("no MIB sources found", file=sys.stderr)
        return 2
    print(f"compiling {len(modules)} module(s) into {config.compiled_mibs_dir()}")

    def on_progress(result, done, total):  # type: ignore[no-untyped-def]
        marker = "✓" if result.ok else "✗"
        print(f"  [{done}/{total}] {marker} {result.module}: {result.status}")

    results = compiler.compile(
        modules, sources,
        rebuild=args.rebuild,
        use_network=args.use_network,
        on_progress=on_progress)

    failed = [r for r in results if not r.ok]
    if failed:
        print(f"\n{len(failed)} module(s) failed:", file=sys.stderr)
        for r in failed:
            print(f"  {r.module}: {r.status}", file=sys.stderr)
        return 1
    return 0


# --- sniff-traps ---------------------------------------------------------


def _print_trap(ev: TrapEvent) -> None:
    print(f"[{ev.received_at:.0f}] {ev.source_ip}:{ev.source_port}"
          f" v{ev.version} community={ev.community} trap_oid={ev.trap_oid}"
          f" uptime={ev.uptime}")
    for oid, type_name, value in ev.var_binds:
        print(f"    {oid}\t{type_name}\t{value}")


def cmd_sniff_traps(args: argparse.Namespace) -> int:
    """UDP trap listener; prints each parsed trap until interrupted."""
    stop = threading.Event()
    sub = UdpTrapSubscription(
        port=args.port,
        on_trap=_print_trap,
        accept_from=args.accept_from or "")
    try:
        sub.start()
    except PermissionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"listening for traps on UDP/{args.port}"
          + (f" (accepting from {args.accept_from})" if args.accept_from else "")
          + "; Ctrl-C to stop")
    try:
        while not stop.is_set():
            stop.wait(timeout=1.0)
    except KeyboardInterrupt:
        print("", file=sys.stderr)
    finally:
        sub.stop()
    return 0


# --- argparse ------------------------------------------------------------


def _add_agent_overrides(p: argparse.ArgumentParser) -> None:
    """Common --host / --port / --community / --version flags for any
    subcommand that talks SNMP."""
    p.add_argument("--host", help="Override agent host (default: settings).")
    p.add_argument("--port", type=int,
                   help="Override agent port (default: settings).")
    p.add_argument("--community",
                   help="Override read community (default: settings).")
    p.add_argument("--version", choices=("1", "2c"),
                   help="SNMP version (default: settings).")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pymibbrowser-cli",
        description="Command-line frontend for the pymibbrowser engine.")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Execute a script file.")
    run.add_argument("script", help="Path to the iReasoning-style script.")
    _add_agent_overrides(run)
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

    walk = sub.add_parser("walk", help="SNMP walk a subtree.")
    walk.add_argument("oid", help="Root OID (numeric or symbolic).")
    _add_agent_overrides(walk)
    walk.set_defaults(func=cmd_walk)

    cmib = sub.add_parser(
        "compile-mibs", help="Compile MIB sources into the local cache.")
    cmib.add_argument(
        "--source", action="append", metavar="DIR",
        help="MIB source directory (repeatable). Defaults to bundled mibs-src/.")
    cmib.add_argument(
        "--use-network", action="store_true",
        help="Fetch missing dependencies from mibs.pysnmp.com.")
    cmib.add_argument(
        "--rebuild", action="store_true",
        help="Recompile even modules already cached.")
    cmib.set_defaults(func=cmd_compile_mibs)

    snf = sub.add_parser(
        "sniff-traps", help="Listen for SNMP traps and print them.")
    snf.add_argument("--port", type=int, default=162,
                     help="UDP port to bind (default: 162; needs root for <1024).")
    snf.add_argument("--accept-from", metavar="CIDR",
                     help="Comma-separated allow-list of source IPs/CIDRs.")
    snf.set_defaults(func=cmd_sniff_traps)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
