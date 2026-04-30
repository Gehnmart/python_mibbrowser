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
    command's last result.

    ``lhs`` is the left-hand operand of the comparison. The legacy
    iReasoning form ``if $ > 50 sound`` uses a bare ``$`` to mean
    "last command's result" — that's the default and keeps existing
    scripts working. Any other ``$NAME`` token is $-substituted at run
    time, so ``if $now > $prev sound`` compares two captured values."""
    predicate: str
    operand: str            # "" for predicate=='err'
    action: str             # 'sound' | 'email' | 'sleep'
    arg: str                # action argument (e.g. recipient or seconds)
    lhs: str = "$"          # "$" = last result; "$NAME" = bound variable


@dataclass(frozen=True)
class Print:
    """Emit ``message`` through the Logger port. ``$`` substitution
    applies, so ``print "uptime is $last"`` interpolates the most recent
    result."""
    message: str


@dataclass(frozen=True)
class Notify:
    """Desktop notification via the Notifier port. Adapters route to
    libnotify / osascript / etc.; runners without a notifier fall back
    to a ``[notify]``-tagged log line."""
    message: str


@dataclass(frozen=True)
class Abort:
    """Stop the script. Any commands after this — including those
    inside enclosing if-blocks — are skipped."""


@dataclass(frozen=True)
class IfBlock:
    """Block-form conditional: ``if <lhs> <op> <rhs>`` … ``else`` …
    ``end``.

    Both bodies are tuples of Commands (recursive — IfBlocks nest).
    The else branch may be empty, meaning "do nothing when the
    predicate is false". Distinct from ``If`` (the legacy one-liner
    with an inline action) so each form keeps a clean shape."""
    predicate: str           # "err" or one of >, <, >=, <=, =, !=
    operand: str             # "" when predicate == "err"
    lhs: str                 # "$" (last result) or "$NAME"
    then_body: tuple[Command, ...]
    else_body: tuple[Command, ...]


@dataclass(frozen=True)
class Let:
    """Bind a variable for use in later commands.

    References take the form ``$NAME`` in any subsequent command's
    string field — host, oid, set value, save target, if operand/arg,
    or another let value. Names are ``[A-Za-z_][A-Za-z0-9_]*``.
    Built-ins always available without an explicit let:
      * ``$last`` — display value of the most recent SNMP result;
      * ``$err``  — last error flag, ``"0"`` or ``"1"``.
    Let-values are themselves substituted at run time, so
    ``let prev $last`` snapshots a result before the next command
    overwrites it."""
    name: str
    value: str


@dataclass(frozen=True)
class Unknown:
    """A line the parser couldn't classify. Kept as an AST node so the
    runner can emit the same diagnostic the original script_runner did,
    instead of silently dropping the line."""
    text: str
    reason: str             # "unknown command" | "invalid if" | "bad sleep"


Command = (Get | GetNext | Set | Sleep | Save | If | IfBlock | Let
            | Print | Notify | Abort | Unknown)


@dataclass(frozen=True)
class Script:
    """Top-level program. Commands run in order."""
    commands: tuple[Command, ...]
