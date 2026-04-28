"""Parse iReasoning-style SNMP script text into an AST.

Pure: given text, returns a Script. Does not resolve OIDs, read files, or
talk to SNMP. Comments (#) and blank lines are dropped silently;
unrecognised commands become Unknown nodes so the runner can emit the
same diagnostics the original implementation did.
"""
from __future__ import annotations

import re

from .ast import Command, Get, GetNext, If, Save, Script, Set, Sleep, Unknown


_IF_ERR_RE = re.compile(r"if\s+\$\s+err\s+(\w+)(?:\s+(.+))?$")
_IF_CMP_RE = re.compile(
    r"if\s+\$\s+(>=|<=|!=|>|<|=)\s*(\S+)\s+(\w+)(?:\s+(.+))?$")


def _parse_host(spec: str, default_port: int) -> tuple[str, int]:
    if ":" in spec:
        host, port_s = spec.rsplit(":", 1)
        try:
            return host, int(port_s)
        except ValueError:
            return spec, default_port
    return spec, default_port


def parse_command(line: str, default_port: int = 161) -> Command:
    """Parse one stripped, non-empty, non-comment line into a Command.

    Always returns a Command — never None. Unparseable input becomes
    Unknown(text=line, reason=...) so the caller can render a diagnostic
    rather than silently dropping the line."""
    parts = line.split()
    op = parts[0].lower()

    if op == "if":
        m = _IF_ERR_RE.match(line)
        if m:
            action, arg = m.groups()
            return If(predicate="err", operand="",
                      action=action, arg=arg or "")
        m = _IF_CMP_RE.match(line)
        if m:
            pred, operand, action, arg = m.groups()
            return If(predicate=pred, operand=operand,
                      action=action, arg=arg or "")
        return Unknown(text=line, reason="invalid if")

    if op == "sleep":
        if len(parts) < 2:
            return Unknown(text=line, reason="bad sleep")
        try:
            return Sleep(seconds=float(parts[1]))
        except ValueError:
            return Unknown(text=parts[1], reason="bad sleep")

    if op == "save":
        if len(parts) < 2:
            return Unknown(text=line, reason="unknown command")
        return Save(target=" ".join(parts[1:]))

    if op in ("get", "getnext"):
        if len(parts) < 3:
            return Unknown(text=line, reason="unknown command")
        host, port = _parse_host(parts[1], default_port)
        oids = tuple(parts[2:])
        return Get(host=host, port=port, oids=oids) if op == "get" \
            else GetNext(host=host, port=port, oids=oids)

    if op == "set":
        # set <host> <oid> <type> <val> [<oid> <type> <val> ...]
        if len(parts) < 5:
            return Unknown(text=line, reason="unknown command")
        host, port = _parse_host(parts[1], default_port)
        rest = parts[2:]
        if len(rest) % 3 != 0:
            return Unknown(text=line, reason="unknown command")
        triples = tuple(
            (rest[i], rest[i + 1], rest[i + 2])
            for i in range(0, len(rest), 3))
        return Set(host=host, port=port, triples=triples)

    return Unknown(text=line, reason="unknown command")


def parse_script(text: str, default_port: int = 161) -> Script:
    """Parse a multi-line script into an AST. Empty / comment lines are
    dropped (no AST node for them — they have nothing to dispatch on)."""
    commands: list[Command] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        commands.append(parse_command(line, default_port))
    return Script(commands=tuple(commands))
