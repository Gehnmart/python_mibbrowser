"""Deterministic execution engine.

Given a Script and an ExecutionContext (which packages every port), run the
script. Every side effect — SNMP, sleep, output, save — flows through the
context's ports. No globals, no module-level state, no library imports
beyond stdlib + typing.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from .ast import (
    Abort,
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
from .model import Agent, VarBind
from .ports import Clock, Logger, Notifier, OutputSink, Resolver, SnmpTransport

_SUBST_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


@dataclass
class ExecutionContext:
    """Bundle of ports + base credentials. The runner owns no state of
    its own — everything that survives across commands lives here or in
    the per-call _RunState. ``notifier`` is optional: scripts that don't
    use ``notify`` never touch it, and a missing notifier is degraded
    to a tagged log line at the call site."""
    agent: Agent
    snmp: SnmpTransport
    clock: Clock
    resolver: Resolver
    logger: Logger
    sink: OutputSink
    cancel: Callable[[], bool] = lambda: False
    notifier: Notifier | None = None


def execute(script: Script, ctx: ExecutionContext) -> None:
    """Run a script through to completion (or first cancel / abort).
    Pure dispatch — every command handler returns when done; the next
    is chosen by isinstance check on the AST node."""
    state = _RunState()
    _execute_commands(script.commands, ctx, state)
    ctx.sink.close()


def _execute_commands(commands: tuple, ctx: ExecutionContext,
                       state: "_RunState") -> None:
    """Run a flat sequence of commands. Used both for the top-level
    script and for if-block bodies. Cancellation and ``abort`` are
    checked at every step so they propagate out of arbitrarily-deep
    nested blocks."""
    for cmd in commands:
        if ctx.cancel():
            ctx.logger.log("[cancelled]")
            return
        if state.aborted:
            return
        if isinstance(cmd, Sleep):
            _interruptible_sleep(cmd.seconds, ctx)
        elif isinstance(cmd, Save):
            ctx.sink.open(_subst(cmd.target, state))
        elif isinstance(cmd, (Get, GetNext)):
            _run_get_or_next(cmd, ctx, state)
        elif isinstance(cmd, Set):
            _run_set(cmd, ctx, state)
        elif isinstance(cmd, If):
            _run_if(cmd, ctx, state)
        elif isinstance(cmd, IfBlock):
            _run_if_block(cmd, ctx, state)
        elif isinstance(cmd, Let):
            # Right-hand side is itself substituted, so `let prev $last`
            # snapshots the current result before subsequent gets
            # overwrite state.last_result.
            state.vars[cmd.name] = _subst(cmd.value, state)
        elif isinstance(cmd, Print):
            ctx.logger.log(_subst(cmd.message, state))
        elif isinstance(cmd, Notify):
            _emit_notify(ctx, _subst(cmd.message, state))
        elif isinstance(cmd, Abort):
            ctx.logger.log("[abort]")
            state.aborted = True
        elif isinstance(cmd, Unknown):
            _log_unknown(cmd, ctx)


def _emit_notify(ctx: ExecutionContext, message: str) -> None:
    """Route a notify through ctx.notifier if wired; otherwise fall
    back to a tagged log line so the message isn't lost on systems
    without a desktop-notification backend."""
    notifier = ctx.notifier
    if notifier is not None:
        try:
            notifier.notify(message)
            return
        except Exception as exc:
            ctx.logger.log(f"[notify failed] {exc}")
    ctx.logger.log(f"[notify] {message}")


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


@dataclass
class _RunState:
    """Per-run carry. last_result feeds the comparison form of `if $`;
    last_error feeds the error form. ``vars`` holds user-defined
    bindings from ``let`` commands. ``aborted`` is flipped by an
    ``abort`` action (one-liner) or command (block) and propagates
    through every level of nesting."""
    last_result: str | None = None
    last_error: int = 0
    vars: dict[str, str] = field(default_factory=dict)
    aborted: bool = False


def _subst(text: str, state: _RunState) -> str:
    """Expand ``$NAME`` references using state.vars + built-ins.

    Built-in names: ``last`` → state.last_result (empty string if no
    command has run yet), ``err`` → string of state.last_error. Unknown
    names are left as the literal ``$NAME`` so the failure surfaces in
    the next layer (resolver / SNMP) with a useful diagnostic instead
    of a silently empty token."""
    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name == "last":
            return state.last_result if state.last_result is not None else ""
        if name == "err":
            return str(state.last_error)
        return state.vars.get(name, m.group(0))
    return _SUBST_RE.sub(repl, text)


def _resolve_target(host_field: str, port_field: int,
                     state: _RunState) -> tuple[str, int]:
    """Substitute $-vars in the host field and re-split host:port if
    the expansion now contains a colon. The parser couldn't tell from
    a bare ``$h`` whether the var also packs a port, so we retry the
    split after substitution. ``let h 192.168.1.1:11161`` followed by
    ``get $h sysUpTime.0`` therefore lands the correct port."""
    sub = _subst(host_field, state)
    if ":" in sub:
        h, _, p = sub.rpartition(":")
        try:
            return h, int(p)
        except ValueError:
            return sub, port_field
    return sub, port_field


def _agent_for(host: str, port: int, base: Agent) -> Agent:
    """Return a copy of base with host/port overridden. We never mutate
    the caller's agent — that would smear state between commands."""
    fields = vars(base).copy()
    fields["host"] = host
    fields["port"] = port
    return Agent(**fields)


def _resolve_oids(oids: tuple[str, ...], ctx: ExecutionContext,
                   state: _RunState) -> list[tuple[int, ...]]:
    """Resolve each OID via the resolver; log + flag last_error for any
    that fail; return only the successful ones. OIDs are $-substituted
    before resolution, so ``let oid sysUpTime.0; get host $oid`` works."""
    out: list[tuple[int, ...]] = []
    for o in oids:
        sub = _subst(o, state)
        t = ctx.resolver.resolve(sub)
        if t is None:
            ctx.logger.log(f"unresolved OID: {sub}")
            state.last_error = 1
            continue
        out.append(t)
    return out


def _run_get_or_next(cmd: Get | GetNext, ctx: ExecutionContext,
                      state: _RunState) -> None:
    op_name = "get" if isinstance(cmd, Get) else "getnext"
    host, port = _resolve_target(cmd.host, cmd.port, state)
    agent = _agent_for(host, port, ctx.agent)
    resolved = _resolve_oids(cmd.oids, ctx, state)
    if not resolved:
        return
    fn = ctx.snmp.get if isinstance(cmd, Get) else ctx.snmp.get_next
    try:
        vbs = fn(agent, resolved)
        state.last_error = 0
    except Exception as exc:
        ctx.logger.log(f"{op_name} {host}:{port}: {exc}")
        state.last_error = 1
        return
    _emit_varbinds(vbs, ctx, state)


def _run_set(cmd: Set, ctx: ExecutionContext, state: _RunState) -> None:
    host, port = _resolve_target(cmd.host, cmd.port, state)
    agent = _agent_for(host, port, ctx.agent)
    pairs: list[tuple[tuple[int, ...], str, str]] = []
    for oid_text, type_tag, raw_val in cmd.triples:
        oid_sub = _subst(oid_text, state)
        val_sub = _subst(raw_val, state)
        t = ctx.resolver.resolve(oid_sub)
        if t is None:
            ctx.logger.log(f"unresolved OID: {oid_sub}")
            state.last_error = 1
            continue
        pairs.append((t, type_tag, val_sub))
    if not pairs:
        return
    try:
        vbs = ctx.snmp.set(agent, pairs)
        state.last_error = 0
    except Exception as exc:
        ctx.logger.log(f"set: {exc}")
        state.last_error = 1
        return
    _emit_varbinds(vbs, ctx, state)


def _emit_varbinds(vbs: list[VarBind], ctx: ExecutionContext,
                    state: _RunState) -> None:
    for vb in vbs:
        line = f".{'.'.join(map(str, vb.oid))}\t{vb.type_name}\t{vb.display_value}"
        ctx.logger.log(line)
        ctx.sink.emit(line)
    if vbs:
        state.last_result = vbs[-1].display_value


def _run_if(cmd: If, ctx: ExecutionContext, state: _RunState) -> None:
    fired = False
    if cmd.predicate == "err":
        # The err predicate inspects the last_error flag regardless of
        # which LHS the user wrote — `if $ err sleep 1` and
        # `if $whatever err sleep 1` mean the same thing.
        fired = state.last_error != 0
    else:
        if cmd.lhs == "$":
            # Legacy bare-$ form: compare against the last command's
            # result. None when no command has run yet, in which case
            # _compare bails out with False — preserves the old
            # "no prior command → no fire" behaviour.
            left_value = state.last_result
        else:
            sub = _subst(cmd.lhs, state)
            # Unresolved $NAME comes back unchanged — degrade to None
            # so _compare returns False instead of trying float("$foo").
            left_value = sub if sub != cmd.lhs else None
        operand = _subst(cmd.operand, state)
        fired = _compare(left_value, operand, cmd.predicate)

    if not fired:
        return

    arg = _subst(cmd.arg, state)
    if cmd.action == "sleep":
        try:
            _interruptible_sleep(float(arg), ctx)
        except (TypeError, ValueError):
            pass
    elif cmd.action == "sound":
        # Bell character — adapters route to terminal/OS notification.
        ctx.logger.log("\a")
    elif cmd.action == "email":
        ctx.logger.log(
            f"[email action → {arg}] (SMTP not configured, skipped)")
    elif cmd.action == "print":
        ctx.logger.log(arg)
    elif cmd.action == "notify":
        _emit_notify(ctx, arg)
    elif cmd.action == "abort":
        ctx.logger.log("[abort]")
        state.aborted = True


def _run_if_block(cmd: IfBlock, ctx: ExecutionContext,
                   state: "_RunState") -> None:
    """Block-form conditional. Evaluates the predicate exactly like
    ``_run_if`` and dispatches to the then- or else-body."""
    if cmd.predicate == "err":
        fired = state.last_error != 0
    else:
        if cmd.lhs == "$":
            left_value = state.last_result
        else:
            sub = _subst(cmd.lhs, state)
            left_value = sub if sub != cmd.lhs else None
        operand = _subst(cmd.operand, state)
        fired = _compare(left_value, operand, cmd.predicate)
    body = cmd.then_body if fired else cmd.else_body
    _execute_commands(body, ctx, state)


_COMPARE_OPS: dict[str, Callable[[float, float], bool]] = {
    "<":  lambda a, b: a < b,
    ">":  lambda a, b: a > b,
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "=":  lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


_NUMERIC_TAIL_RE = re.compile(r"\(\s*([+-]?\d+(?:\.\d+)?)\s*\)\s*$")
_NUMERIC_HEAD_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?")


def _coerce_numeric(s: str | None) -> float | None:
    """Best-effort number extraction. Plain digits parse directly;
    TimeTicks display values like ``"1 day 15 hours … (14176126)"``
    yield the trailing parenthesised raw count (the unit users actually
    threshold on); ``"111 packets"`` yields the leading integer."""
    if not s:
        return None
    text = str(s).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    m = _NUMERIC_TAIL_RE.search(text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    m = _NUMERIC_HEAD_RE.match(text)
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            pass
    return None


def _compare(left: str | None, right: str, op: str) -> bool:
    """Best-effort numeric comparison. Falls through to False on any
    parse error or unknown operator — matches the original behaviour
    where a non-numeric value silently disables the predicate."""
    cur = _coerce_numeric(left)
    val = _coerce_numeric(right)
    if cur is None or val is None:
        return False
    fn = _COMPARE_OPS.get(op)
    return False if fn is None else fn(cur, val)


def _interruptible_sleep(total: float, ctx: ExecutionContext) -> None:
    """Sleep that wakes every 100 ms to honour ctx.cancel(). Lets a
    `sleep 3600` bail in <0.1 s instead of waiting an hour at shutdown."""
    step = 0.1
    remaining = total
    while remaining > 0:
        if ctx.cancel():
            return
        chunk = step if remaining > step else remaining
        ctx.clock.sleep(chunk)
        remaining -= chunk


def _log_unknown(cmd: Unknown, ctx: ExecutionContext) -> None:
    if cmd.reason == "bad sleep":
        ctx.logger.log(f"bad sleep: {cmd.text}")
    elif cmd.reason == "invalid if":
        ctx.logger.log(f"skip: invalid if: {cmd.text}")
    else:
        ctx.logger.log(f"unknown command: {cmd.text}")
