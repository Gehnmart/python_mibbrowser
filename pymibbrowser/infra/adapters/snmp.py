"""SnmpTransport adapter — engine.ports.SnmpTransport via infra.snmp_ops.

Pure translation: each engine call drops into the matching op_*, and the
pysnmp-bound VarBind is rewrapped as the engine's pure VarBind (without
the rfc1902 value object). No state — every call is fresh."""
from __future__ import annotations

from ...engine.model import Agent, VarBind
from .. import snmp_ops
from ..snmp_ops import VarBind as _RawVarBind


def _strip(raw: list[_RawVarBind]) -> list[VarBind]:
    return [VarBind(oid=v.oid, type_name=v.type_name,
                     display_value=v.display_value) for v in raw]


class PysnmpTransport:
    """Synchronous pysnmp-backed transport. Constructible with no args —
    no per-instance state. Multiple ExecutionContexts can share one
    instance.

    Calls go through ``snmp_ops`` as module attribute lookups (rather
    than bound imports) so tests can monkeypatch the underlying ops
    without touching the adapter."""

    def get(self, agent: Agent,
             oids: list[tuple[int, ...]]) -> list[VarBind]:
        return _strip(snmp_ops.op_get(agent, oids))

    def get_next(self, agent: Agent,
                  oids: list[tuple[int, ...]]) -> list[VarBind]:
        return _strip(snmp_ops.op_next(agent, oids))

    def set(self, agent: Agent,
             pairs: list[tuple[tuple[int, ...], str, str]]) -> list[VarBind]:
        # Encode (oid, tag, raw) → (oid, rfc1902.X) at the boundary, so
        # the engine never sees a vendor type. build_set_value owns the
        # tag→type mapping; the adapter just calls it.
        encoded = [(oid, snmp_ops.build_set_value(tag, raw))
                    for oid, tag, raw in pairs]
        return _strip(snmp_ops.op_set(agent, encoded))
