"""Parse iReasoning-style SNMP script text into an AST.

Pure: given text, returns a Script. Does not resolve OIDs, read files, or
talk to SNMP. Comments (#) and blank lines are dropped silently;
unrecognised commands become Unknown nodes so the runner can emit the
same diagnostics the original implementation did.
"""
from __future__ import annotations

import re

from .ast import (
    Abort,
    Command,
    Get,
    GetNext,
    If,
    IfBlock,
    Let,
    Notify,
    Print,
    Save,
    Script,
    Set,
    Sleep,
    Unknown,
)

_IF_ERR_RE = re.compile(r"if\s+(\$\w*)\s+err\s+(\w+)(?:\s+(.+))?$")
_IF_CMP_RE = re.compile(
    r"if\s+(\$\w*)\s+(>=|<=|!=|>|<|=)\s*(\S+)\s+(\w+)(?:\s+(.+))?$")
# Block-form headers: same shape but no trailing action token. The block
# body is read from subsequent lines until `else` or `end`.
_IF_BLOCK_ERR_RE = re.compile(r"if\s+(\$\w*)\s+err\s*$")
_IF_BLOCK_CMP_RE = re.compile(
    r"if\s+(\$\w*)\s+(>=|<=|!=|>|<|=)\s*(\S+)\s*$")
_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _strip_quotes(s: str) -> str:
    """Drop a surrounding pair of "..." or '...' so users can write
    ``print "hello world"`` without leaking the quotes into output.
    Single quotes are stripped only as a matching pair, not mid-string."""
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


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
            lhs, action, arg = m.groups()
            return If(predicate="err", operand="",
                      action=action, arg=arg or "", lhs=lhs)
        m = _IF_CMP_RE.match(line)
        if m:
            lhs, pred, operand, action, arg = m.groups()
            return If(predicate=pred, operand=operand,
                      action=action, arg=arg or "", lhs=lhs)
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

    if op == "print":
        if len(parts) < 2:
            return Print(message="")
        return Print(message=_strip_quotes(line.split(None, 1)[1]))

    if op == "notify":
        if len(parts) < 2:
            return Notify(message="")
        return Notify(message=_strip_quotes(line.split(None, 1)[1]))

    if op == "abort":
        return Abort()

    if op == "let":
        # `let NAME VALUE` or `let NAME = VALUE`. VALUE is the rest of
        # the line so it can contain spaces (treated as a literal
        # string; substitution happens at run time on whichever fields
        # consume it).
        if len(parts) < 3:
            return Unknown(text=line, reason="unknown command")
        name = parts[1]
        if not _VAR_NAME_RE.match(name):
            return Unknown(text=line, reason="unknown command")
        rest = parts[2:]
        if rest[0] == "=":
            rest = rest[1:]
            if not rest:
                return Unknown(text=line, reason="unknown command")
        return Let(name=name, value=" ".join(rest))

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
    dropped. Block-form ``if`` (no inline action after the operand)
    consumes following lines into a then-body, then optionally an
    else-body, terminated by ``end`` — bash-style. Blocks nest."""
    significant = [raw.strip() for raw in text.splitlines()
                   if raw.strip() and not raw.strip().startswith("#")]
    it = iter(significant)
    commands, terminator = _parse_block(it, default_port, ())
    if terminator:
        # `else` / `end` at the top level — orphan, surface as Unknown
        # so the user sees a diagnostic instead of silently losing the
        # tail of the script.
        commands.append(Unknown(text=terminator,
                                 reason="unknown command"))
    return Script(commands=tuple(commands))


def _parse_block(it, default_port: int,
                  end_tokens: tuple[str, ...]) -> tuple[list[Command], str]:
    """Read commands until one of ``end_tokens`` (e.g. ``("else", "end")``)
    appears as the line's first token, or until the iterator is
    exhausted. Returns ``(commands, terminator)`` where ``terminator``
    is the matched end-token (``""`` on EOF). The terminator line is
    consumed before returning."""
    commands: list[Command] = []
    for line in it:
        first = line.split()[0].lower()
        if first in end_tokens:
            return commands, first
        # Block-form `if` has no inline action — consume next lines
        # into bodies. One-liner `if` continues to flow through
        # parse_command below.
        block = _try_parse_if_block_header(line)
        if block is not None:
            then_body, term = _parse_block(
                it, default_port, ("else", "end"))
            else_body: list[Command] = []
            if term == "else":
                else_body, term2 = _parse_block(
                    it, default_port, ("end",))
                if term2 != "end":
                    commands.append(Unknown(text=line,
                                             reason="invalid if"))
                    continue
            elif term != "end":
                # Hit EOF without `end` — log and drop the malformed
                # block. Better than emitting a half-built IfBlock that
                # would mislead the runner.
                commands.append(Unknown(text=line,
                                         reason="invalid if"))
                continue
            commands.append(IfBlock(
                predicate=block["predicate"],
                operand=block["operand"],
                lhs=block["lhs"],
                then_body=tuple(then_body),
                else_body=tuple(else_body),
            ))
            continue
        commands.append(parse_command(line, default_port))
    return commands, ""


def _try_parse_if_block_header(line: str) -> dict | None:
    """Recognise ``if $ > 50`` / ``if $now err`` (no trailing action)
    and return the parsed pieces. Returns None for one-liners and for
    non-`if` lines."""
    m = _IF_BLOCK_ERR_RE.match(line)
    if m:
        return {"predicate": "err", "operand": "", "lhs": m.group(1)}
    m = _IF_BLOCK_CMP_RE.match(line)
    if m:
        return {"predicate": m.group(2), "operand": m.group(3),
                "lhs": m.group(1)}
    return None
