"""
SNMP operations wrapper around pysnmp v7.

pysnmp v7 API is asyncio-first; we expose simple sync helpers that run each
call in a fresh asyncio loop. The UI launches these in a QThread so the event
loop doesn't block.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    UsmUserData,
    bulk_cmd,
    get_cmd,
    is_end_of_mib,
    next_cmd,
    set_cmd,
    usm3DESEDEPrivProtocol,
    usmAesCfb128Protocol,
    usmAesCfb192Protocol,
    usmAesCfb256Protocol,
    usmDESPrivProtocol,
    usmHMACMD5AuthProtocol,
    usmHMACSHAAuthProtocol,
    usmNoAuthProtocol,
    usmNoPrivProtocol,
)
from pysnmp.proto import rfc1902

from .config import Agent

log = logging.getLogger(__name__)


AUTH_PROTOS = {
    "none": usmNoAuthProtocol,
    "md5": usmHMACMD5AuthProtocol,
    "sha": usmHMACSHAAuthProtocol,
}
PRIV_PROTOS = {
    "none": usmNoPrivProtocol,
    "des": usmDESPrivProtocol,
    "3des": usm3DESEDEPrivProtocol,
    "aes": usmAesCfb128Protocol,
    "aes128": usmAesCfb128Protocol,
    "aes192": usmAesCfb192Protocol,
    "aes256": usmAesCfb256Protocol,
}


@dataclass
class VarBind:
    """One (oid, type, value) triple returned from an SNMP op."""
    oid: tuple[int, ...]
    type_name: str
    value: Any
    display_value: str = ""

    @classmethod
    def from_pysnmp(cls, name, val) -> VarBind:
        oid = tuple(int(x) for x in name.asTuple())
        tn = val.__class__.__name__
        return cls(oid=oid, type_name=tn, value=val, display_value=_display(val))


def _format_timeticks(hundredths: int) -> str:
    """Human-readable TimeTicks: '1 day 2 hours 3 minutes 12.34 seconds (N)'."""
    if hundredths < 0:
        return str(hundredths)
    parts = []
    remainder = hundredths
    if remainder >= 8_640_000:
        days, remainder = divmod(remainder, 8_640_000)
        parts.append(f"{days} day" + ("" if days == 1 else "s"))
    if remainder >= 360_000:
        hours, remainder = divmod(remainder, 360_000)
        parts.append(f"{hours} hour" + ("" if hours == 1 else "s"))
    if remainder >= 6_000:
        minutes, remainder = divmod(remainder, 6_000)
        parts.append(f"{minutes} minute" + ("" if minutes == 1 else "s"))
    parts.append(f"{remainder / 100:.2f} seconds")
    return f"{' '.join(parts)} ({hundredths})"


def _display(val: Any) -> str:
    """Pretty-print a value for the result table."""
    try:
        if isinstance(val, rfc1902.TimeTicks):
            return _format_timeticks(int(val))
        # IpAddress is a 4-byte OctetString subclass — must check before the
        # generic OctetString branch, otherwise it renders as hex bytes.
        if isinstance(val, rfc1902.IpAddress):
            raw = bytes(val.asOctets()) if hasattr(val, "asOctets") else bytes(val)
            if len(raw) == 4:
                return ".".join(str(b) for b in raw)
            return val.prettyPrint()
        if isinstance(val, rfc1902.OctetString):
            raw = bytes(val.asOctets()) if hasattr(val, "asOctets") else bytes(val)
            if all(32 <= b < 127 or b in (9, 10, 13) for b in raw):
                try:
                    return raw.decode("utf-8")
                except UnicodeDecodeError:
                    pass
            return " ".join(f"{b:02X}" for b in raw)
        if isinstance(val, rfc1902.ObjectIdentifier):
            return "." + ".".join(str(p) for p in val.asTuple())
        return val.prettyPrint()
    except Exception:
        return str(val)


@dataclass
class SnmpError(Exception):
    message: str
    def __str__(self) -> str:
        return self.message


# ---------------------------------------------------------------------------
# Auth builders
# ---------------------------------------------------------------------------

def _build_auth(agent: Agent, for_write: bool = False):
    if agent.version in ("1", "2c"):
        community = agent.write_community if for_write else agent.read_community
        mp = 0 if agent.version == "1" else 1
        return CommunityData(community, mpModel=mp)
    # v3
    auth = AUTH_PROTOS.get(agent.auth_protocol.lower(), usmNoAuthProtocol)
    priv = PRIV_PROTOS.get(agent.priv_protocol.lower(), usmNoPrivProtocol)
    return UsmUserData(
        agent.user,
        agent.auth_password or None,
        agent.priv_password or None,
        authProtocol=auth,
        privProtocol=priv,
    )


async def _build_target(agent: Agent) -> UdpTransportTarget:
    return await UdpTransportTarget.create(
        (agent.host, agent.port),
        timeout=agent.timeout_s,
        retries=agent.retries,
    )


def _parse_oid(oid: str | Iterable[int]) -> ObjectIdentity:
    if isinstance(oid, str):
        s = oid.strip().lstrip(".")
        if all(p.isdigit() for p in s.split(".")):
            return ObjectIdentity(rfc1902.ObjectName(s))
        # Symbolic — delegated to pysnmp's MibViewController if any; otherwise
        # caller should have already resolved.
        return ObjectIdentity(s)
    tup = tuple(int(x) for x in oid)
    return ObjectIdentity(rfc1902.ObjectName(".".join(str(p) for p in tup)))


# ---------------------------------------------------------------------------
# Public helpers (async)
# ---------------------------------------------------------------------------

async def async_get(agent: Agent, oids: list) -> list[VarBind]:
    engine = SnmpEngine()
    target = await _build_target(agent)
    try:
        err_ind, err_stat, err_idx, var_binds = await get_cmd(
            engine,
            _build_auth(agent),
            target,
            ContextData(),
            *[ObjectType(_parse_oid(o)) for o in oids],
        )
        if err_ind:
            raise SnmpError(str(err_ind))
        if err_stat:
            raise SnmpError(f"{err_stat.prettyPrint()} at #{err_idx}")
        return [VarBind.from_pysnmp(n, v) for n, v in var_binds]
    finally:
        engine.close_dispatcher()


async def async_next(agent: Agent, oids: list) -> list[VarBind]:
    engine = SnmpEngine()
    target = await _build_target(agent)
    try:
        err_ind, err_stat, err_idx, var_binds = await next_cmd(
            engine,
            _build_auth(agent),
            target,
            ContextData(),
            *[ObjectType(_parse_oid(o)) for o in oids],
        )
        if err_ind:
            raise SnmpError(str(err_ind))
        if err_stat:
            raise SnmpError(f"{err_stat.prettyPrint()} at #{err_idx}")
        return [VarBind.from_pysnmp(n, v) for n, v in var_binds]
    finally:
        engine.close_dispatcher()


async def async_bulk(agent: Agent, oids: list) -> list[VarBind]:
    engine = SnmpEngine()
    target = await _build_target(agent)
    try:
        err_ind, err_stat, err_idx, var_binds = await bulk_cmd(
            engine,
            _build_auth(agent),
            target,
            ContextData(),
            agent.non_repeaters,
            agent.max_repetitions,
            *[ObjectType(_parse_oid(o)) for o in oids],
        )
        if err_ind:
            raise SnmpError(str(err_ind))
        if err_stat:
            raise SnmpError(f"{err_stat.prettyPrint()} at #{err_idx}")
        return [VarBind.from_pysnmp(n, v) for n, v in var_binds]
    finally:
        engine.close_dispatcher()


async def async_table_walk(agent: Agent, col_oids: list[tuple[int, ...]],
                           on_progress=None) -> list[VarBind]:
    """
    Walk multiple column OIDs in parallel — one GETBULK per round-trip
    returns `max_repetitions` rows × len(col_oids) varbinds atomically.

    Use this for table walks against live tables where row membership changes
    between single-column walks (e.g. tcpConnTable with timeWait connections
    evaporating after 60 s).
    """
    if not col_oids:
        return []
    engine = SnmpEngine()
    target = await _build_target(agent)
    results: list[VarBind] = []
    # Track per-column next OID and whether that column is exhausted.
    current = [list(c) for c in col_oids]
    roots = [tuple(c) for c in col_oids]
    done = [False] * len(col_oids)
    try:
        while not all(done):
            vbs_to_ask = [
                ObjectType(_parse_oid(current[i]))
                for i in range(len(current)) if not done[i]
            ]
            if not vbs_to_ask:
                break
            active_idx = [i for i in range(len(current)) if not done[i]]
            err_ind, err_stat, err_idx, vbs = await bulk_cmd(
                engine, _build_auth(agent), target, ContextData(),
                0, max(agent.max_repetitions, 10),
                *vbs_to_ask,
            )
            if err_ind:
                raise SnmpError(str(err_ind))
            if err_stat:
                raise SnmpError(f"{err_stat.prettyPrint()} at #{err_idx}")
            if not vbs:
                break
            # Bulk response is a flat list: for N varbinds in request and
            # max_repetitions=R, we get back N×R varbinds in
            # repetition-major order: [col0_row0, col1_row0, col2_row0, ...,
            # col0_row1, col1_row1, ...]
            ncols = len(vbs_to_ask)
            progress_made = False
            for offset in range(0, len(vbs), ncols):
                chunk = vbs[offset:offset + ncols]
                for sub_i, (name, val) in enumerate(chunk):
                    col_i = active_idx[sub_i]
                    vb = VarBind.from_pysnmp(name, val)
                    # Left the subtree → this column is done.
                    if vb.oid[: len(roots[col_i])] != roots[col_i]:
                        done[col_i] = True
                        continue
                    if is_end_of_mib([(name, val)]):
                        done[col_i] = True
                        continue
                    results.append(vb)
                    if on_progress is not None:
                        on_progress(vb)
                    current[col_i] = list(vb.oid)
                    progress_made = True
            if not progress_made:
                break
    finally:
        engine.close_dispatcher()
    return results


async def async_walk(agent: Agent, root: str | Iterable[int],
                     on_progress=None) -> list[VarBind]:
    """SNMP walk — iterate GETNEXTs until we leave the subtree."""
    engine = SnmpEngine()
    target = await _build_target(agent)
    start = _parse_oid(root)
    root_tuple: tuple[int, ...]
    if isinstance(root, str):
        s = root.strip().lstrip(".")
        root_tuple = tuple(int(p) for p in s.split(".")) if s.replace(".", "").isdigit() else ()
    else:
        root_tuple = tuple(int(x) for x in root)

    results: list[VarBind] = []
    current = start
    try:
        while True:
            err_ind, err_stat, err_idx, vbs = await next_cmd(
                engine, _build_auth(agent), target, ContextData(),
                ObjectType(current),
            )
            if err_ind:
                raise SnmpError(str(err_ind))
            if err_stat:
                raise SnmpError(f"{err_stat.prettyPrint()} at #{err_idx}")
            if not vbs:
                break
            name, val = vbs[0]
            if is_end_of_mib([(name, val)]):
                break
            vb = VarBind.from_pysnmp(name, val)
            if root_tuple and vb.oid[: len(root_tuple)] != root_tuple:
                break
            results.append(vb)
            if on_progress is not None:
                on_progress(vb)
            current = ObjectIdentity(rfc1902.ObjectName(".".join(str(p) for p in vb.oid)))
    finally:
        engine.close_dispatcher()
    return results


_TYPE_TAGS: dict[str, type] = {
    "i": rfc1902.Integer32,
    "u": rfc1902.Unsigned32,
    "t": rfc1902.TimeTicks,
    "a": rfc1902.IpAddress,
    "o": rfc1902.ObjectName,
    "s": rfc1902.OctetString,
    "c": rfc1902.Counter32,
    "g": rfc1902.Gauge32,
    "x": rfc1902.OctetString,           # hex — hexValue= keyword below
}


def build_set_value(type_tag: str, text: str):
    t = (type_tag or "s").lower()
    cls = _TYPE_TAGS.get(t, rfc1902.OctetString)
    if t == "x":
        hex_str = text.replace(" ", "").replace("0x", "").replace("0X", "")
        return rfc1902.OctetString(hexValue=hex_str)
    if t == "o":
        return rfc1902.ObjectName(text.strip().lstrip("."))
    if t == "s":
        return rfc1902.OctetString(text)
    return cls(text)


async def async_set(agent: Agent, pairs: list[tuple[str, Any]]) -> list[VarBind]:
    engine = SnmpEngine()
    target = await _build_target(agent)
    obj_types = [ObjectType(_parse_oid(o), v) for o, v in pairs]
    try:
        err_ind, err_stat, err_idx, vbs = await set_cmd(
            engine, _build_auth(agent, for_write=True),
            target, ContextData(), *obj_types,
        )
        if err_ind:
            raise SnmpError(str(err_ind))
        if err_stat:
            raise SnmpError(f"{err_stat.prettyPrint()} at #{err_idx}")
        return [VarBind.from_pysnmp(n, v) for n, v in vbs]
    finally:
        engine.close_dispatcher()


# ---------------------------------------------------------------------------
# Sync wrappers used by the UI
# ---------------------------------------------------------------------------


class _LoopRunner:
    """Long-lived asyncio loop on a daemon thread.

    The previous implementation called ``asyncio.run(coro)`` per op,
    paying loop creation + DNS resolver startup + dispatcher init on
    every GET. That's wasteful at the low end and a deadlock hazard
    at the high end (any caller that already had a running loop would
    fall through to a fresh loop on the same thread).

    Pattern: lazy-start one loop on a background thread; submit each
    coroutine via ``run_coroutine_threadsafe`` and block on the
    Future. Engines created inside ``async_*`` helpers still live
    only as long as the call (their ``close_dispatcher()`` runs at
    the end of each coroutine), so resource ownership is unchanged
    — only the loop persists.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _ensure_started(self) -> None:
        with self._lock:
            if self._loop is not None and not self._loop.is_closed():
                return
            ready = threading.Event()
            err: list[BaseException] = []

            def _main() -> None:
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    self._loop = loop
                    ready.set()
                    loop.run_forever()
                except BaseException as e:
                    # Propagate the failure back to the starter thread,
                    # which is sleeping on `ready`. Without this catch
                    # the loop thread would die silently and the starter
                    # would just time out.
                    err.append(e)
                    ready.set()

            self._thread = threading.Thread(
                target=_main, name="snmp-ops-loop", daemon=True)
            self._thread.start()
            if not ready.wait(timeout=5.0):
                raise RuntimeError("snmp-ops-loop failed to start within 5 s")
            if err:
                raise err[0]

    def submit(self, coro: Any) -> Any:
        """Run ``coro`` on the persistent loop and return its result.

        Blocks the caller. Calling from the loop thread itself would
        deadlock (the loop can't make progress while it's blocked on
        a future of its own coroutine), so we reject that explicitly."""
        self._ensure_started()
        if threading.current_thread() is self._thread:
            raise RuntimeError(
                "snmp_ops._run cannot be called from the loop thread itself")
        assert self._loop is not None
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result()

    def shutdown(self) -> None:
        """Stop the loop and join the thread. Idempotent. Tests use
        this for clean teardown; production never calls it (the
        daemon thread dies with the process)."""
        with self._lock:
            loop = self._loop
            thread = self._thread
            self._loop = None
            self._thread = None
        if loop is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=2.0)
        try:
            loop.close()
        except Exception:
            pass


_loop_runner = _LoopRunner()


def _run(coro: Any) -> Any:
    return _loop_runner.submit(coro)


def op_get(agent: Agent, oids: list) -> list[VarBind]:
    return _run(async_get(agent, oids))


def op_next(agent: Agent, oids: list) -> list[VarBind]:
    return _run(async_next(agent, oids))


def op_bulk(agent: Agent, oids: list) -> list[VarBind]:
    return _run(async_bulk(agent, oids))


def op_walk(agent: Agent, oid: str | Iterable[int],
            cb: Callable[[VarBind], None] | None = None) -> list[VarBind]:
    return _run(async_walk(agent, oid, cb))


def op_set(agent: Agent, pairs: list[tuple[str, Any]]) -> list[VarBind]:
    return _run(async_set(agent, pairs))


def op_table_walk(agent: Agent, col_oids: list[tuple[int, ...]],
                  cb: Callable[[VarBind], None] | None = None) -> list[VarBind]:
    return _run(async_table_walk(agent, col_oids, cb))
