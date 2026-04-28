"""Deterministic execution engine.

Given a Script and an ExecutionContext (which packages every port), run the
script. Every side effect — SNMP, sleep, output, save — flows through the
context's ports. No globals, no module-level state, no library imports
beyond stdlib + typing.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .ast import Get, GetNext, If, Save, Script, Set, Sleep, Unknown
from .model import Agent, VarBind
from .ports import Clock, Logger, OutputSink, Resolver, SnmpTransport


@dataclass
class ExecutionContext:
    """Bundle of ports + base credentials. The runner owns no state of
    its own — everything that survives across commands lives here or in
    the per-call _RunState."""
    agent: Agent
    snmp: SnmpTransport
    clock: Clock
    resolver: Resolver
    logger: Logger
    sink: OutputSink
    cancel: Callable[[], bool] = lambda: False


def execute(script: Script, ctx: ExecutionContext) -> None:
    """Run a script through to completion (or first cancel). Pure
    dispatch — every command handler returns when done; the next is
    chosen by isinstance check on the AST node."""
    state = _RunState()
    for cmd in script.commands:
        if ctx.cancel():
            ctx.logger.log("[cancelled]")
            break
        if isinstance(cmd, Sleep):
            _interruptible_sleep(cmd.seconds, ctx)
        elif isinstance(cmd, Save):
            ctx.sink.open(cmd.target)
        elif isinstance(cmd, (Get, GetNext)):
            _run_get_or_next(cmd, ctx, state)
        elif isinstance(cmd, Set):
            _run_set(cmd, ctx, state)
        elif isinstance(cmd, If):
            _run_if(cmd, ctx, state)
        elif isinstance(cmd, Unknown):
            _log_unknown(cmd, ctx)
    ctx.sink.close()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


@dataclass
class _RunState:
    """Per-run carry. last_result feeds the comparison form of `if $`;
    last_error feeds the error form."""
    last_result: str | None = None
    last_error: int = 0


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
    that fail; return only the successful ones."""
    out: list[tuple[int, ...]] = []
    for o in oids:
        t = ctx.resolver.resolve(o)
        if t is None:
            ctx.logger.log(f"unresolved OID: {o}")
            state.last_error = 1
            continue
        out.append(t)
    return out


def _run_get_or_next(cmd: Get | GetNext, ctx: ExecutionContext,
                      state: _RunState) -> None:
    op_name = "get" if isinstance(cmd, Get) else "getnext"
    agent = _agent_for(cmd.host, cmd.port, ctx.agent)
    resolved = _resolve_oids(cmd.oids, ctx, state)
    if not resolved:
        return
    fn = ctx.snmp.get if isinstance(cmd, Get) else ctx.snmp.get_next
    try:
        vbs = fn(agent, resolved)
        state.last_error = 0
    except Exception as exc:
        ctx.logger.log(f"{op_name} {cmd.host}:{cmd.port}: {exc}")
        state.last_error = 1
        return
    _emit_varbinds(vbs, ctx, state)


def _run_set(cmd: Set, ctx: ExecutionContext, state: _RunState) -> None:
    agent = _agent_for(cmd.host, cmd.port, ctx.agent)
    pairs: list[tuple[tuple[int, ...], str, str]] = []
    for oid_text, type_tag, raw_val in cmd.triples:
        t = ctx.resolver.resolve(oid_text)
        if t is None:
            ctx.logger.log(f"unresolved OID: {oid_text}")
            state.last_error = 1
            continue
        pairs.append((t, type_tag, raw_val))
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
    # No prior command yet — nothing to compare against.
    if state.last_result is None and state.last_error == 0:
        return

    fired = False
    if cmd.predicate == "err":
        fired = state.last_error != 0
    else:
        fired = _compare(state.last_result, cmd.operand, cmd.predicate)

    if not fired:
        return

    if cmd.action == "sleep":
        try:
            _interruptible_sleep(float(cmd.arg), ctx)
        except (TypeError, ValueError):
            pass
    elif cmd.action == "sound":
        # Bell character — adapters route to terminal/OS notification.
        ctx.logger.log("\a")
    elif cmd.action == "email":
        ctx.logger.log(
            f"[email action → {cmd.arg}] (SMTP not configured, skipped)")


_COMPARE_OPS: dict[str, Callable[[float, float], bool]] = {
    "<":  lambda a, b: a < b,
    ">":  lambda a, b: a > b,
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "=":  lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def _compare(left: str | None, right: str, op: str) -> bool:
    """Best-effort numeric comparison. Falls through to False on any
    parse error or unknown operator — matches the original behaviour
    where a non-numeric value silently disables the predicate."""
    try:
        cur = float(left) if left else None
        val = float(right) if right else None
    except (TypeError, ValueError):
        return False
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
