"""Thin file-level shim over engine.execute.

Reads the script from disk, hands the text to the parser, builds the
production adapters (pysnmp + MibTree + system clock + file sink), and
runs the AST through the engine. Every interesting decision lives in
``pymibbrowser.engine`` — this module only wires the file boundary.

Kept as a public API for back-compat: existing UI callers do
``script_runner.run(path, agent, tree, logger=cb, should_cancel=cb)`` and
that signature is preserved.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..engine.model import Agent
from ..engine.parser import parse_script
from ..engine.runner import ExecutionContext, execute
from .adapters import (
    CallbackLogger,
    DesktopNotifier,
    FileSink,
    MibTreeResolver,
    PrintLogger,
    PysnmpTransport,
    WallClock,
)
from .mib_loader import MibTree


class ScriptError(Exception):
    pass


def run(path: str, agent: Agent, tree: MibTree,
        logger: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None) -> None:
    """Read ``path``, parse it, and execute against the given agent + MIB
    tree. ``logger`` receives every diagnostic line; ``should_cancel`` is
    polled periodically so the caller can interrupt long-running scripts."""
    text = Path(path).read_text()
    script = parse_script(text, default_port=agent.port)

    log_adapter = CallbackLogger(logger) if logger is not None else PrintLogger()
    # FileSink emits a "saved N lines to <path>" line through the logger
    # at close() — preserves the original UX, where the user sees the
    # final write target on stdout/log pane.
    sink = FileSink(on_persist=lambda p, n: log_adapter.log(
        f"saved {n} line(s) to {p}"))

    ctx = ExecutionContext(
        agent=agent,
        snmp=PysnmpTransport(),
        clock=WallClock(),
        resolver=MibTreeResolver(tree),
        logger=log_adapter,
        sink=sink,
        cancel=should_cancel or (lambda: False),
        notifier=DesktopNotifier(),
    )
    execute(script, ctx)
