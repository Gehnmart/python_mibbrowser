"""Trap adapters — engine.ports.TrapPublisher / TrapSubscription.

The publisher wraps ``infra.trap_sender.send_trap``; the subscription
wraps ``infra.trap_receiver.TrapListener``. Both translate at the
boundary so the engine layer never sees rfc1902 values or the
listener's UI-enriched event type.
"""
from __future__ import annotations

from collections.abc import Callable

from ...engine.model import TrapEvent
from .. import snmp_ops, trap_sender
from ..trap_receiver import TrapEvent as _RawTrapEvent
from ..trap_receiver import TrapListener

# --- Publisher -----------------------------------------------------------

class PysnmpTrapPublisher:
    """Send traps via pysnmp's ``send_notification``. Stateless — every
    send opens a fresh engine and dispatcher."""

    def send(self, host: str, port: int, community: str, version: str,
              trap_oid: str,
              var_binds: list[tuple[str, str, str]]) -> str:
        # Encode at the boundary: (oid, type_tag, raw) → (oid, rfc1902.X).
        # build_set_value owns the type_tag → rfc1902 mapping; this
        # adapter just delegates to it, mirroring SnmpTransport.set.
        encoded = [(oid, snmp_ops.build_set_value(tag, raw))
                    for oid, tag, raw in var_binds]
        return trap_sender.send_trap(host, port, community, version,
                                       trap_oid, encoded)


# --- Subscription --------------------------------------------------------

def _to_engine_event(raw: _RawTrapEvent) -> TrapEvent:
    """Strip the listener's UI-enrichment fields (severity / message)
    and convert to the engine's pure TrapEvent."""
    return TrapEvent(
        received_at=raw.time,
        source_ip=raw.source_ip,
        source_port=raw.source_port,
        version=raw.version,
        community=raw.community,
        trap_oid=raw.trap_oid,
        uptime=raw.uptime,
        enterprise=raw.enterprise,
        generic_trap=raw.generic_trap,
        specific_trap=raw.specific_trap,
        agent_addr=raw.agent_addr,
        var_binds=tuple(raw.var_binds),
        raw_bytes=raw.raw_bytes,
    )


class UdpTrapSubscription:
    """UDP trap subscription backed by a TrapListener thread.

    The on_trap callback is captured at construction — switching
    callbacks means constructing a new subscription. accept_from is the
    same comma-separated CIDR / IP filter the underlying listener uses;
    "" means accept any source."""

    def __init__(self, port: int,
                 on_trap: Callable[[TrapEvent], None],
                 accept_from: str = "") -> None:
        self._listener = TrapListener(
            port=port,
            on_trap=lambda raw: on_trap(_to_engine_event(raw)),
            accept_from=accept_from,
        )

    def start(self) -> None:
        self._listener.start()

    def stop(self) -> None:
        self._listener.stop()

    def is_running(self) -> bool:
        return self._listener.is_running()
