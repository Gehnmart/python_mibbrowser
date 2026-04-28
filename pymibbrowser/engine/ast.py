"""Script AST.

Each command is a frozen dataclass — the engine pattern-matches on type to
dispatch. The parser builds these from text; the runner consumes them.
The AST is the only contract between parser and runner.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Get:
    host: str
    port: int
    oids: tuple[str, ...]   # symbolic or numeric — runner resolves via Resolver


@dataclass(frozen=True)
class GetNext:
    host: str
    port: int
    oids: tuple[str, ...]


@dataclass(frozen=True)
class Set:
    host: str
    port: int
    triples: tuple[tuple[str, str, str], ...]   # (oid_text, type_tag, raw_value)


@dataclass(frozen=True)
class Sleep:
    seconds: float


@dataclass(frozen=True)
class Save:
    target: str             # opaque to the engine — handed to OutputSink.open()


@dataclass(frozen=True)
class If:
    """Conditional action. predicate is 'err' for error-checks, otherwise
    one of '>', '<', '>=', '<=', '=', '!=' compared against the previous
    command's last result."""
    predicate: str
    operand: str            # "" for predicate=='err'
    action: str             # 'sound' | 'email' | 'sleep'
    arg: str                # action argument (e.g. recipient or seconds)


@dataclass(frozen=True)
class Unknown:
    """A line the parser couldn't classify. Kept as an AST node so the
    runner can emit the same diagnostic the original script_runner did,
    instead of silently dropping the line."""
    text: str
    reason: str             # "unknown command" | "invalid if" | "bad sleep"


Command = Get | GetNext | Set | Sleep | Save | If | Unknown


@dataclass(frozen=True)
class Script:
    """Top-level program. Commands run in order."""
    commands: tuple[Command, ...]
