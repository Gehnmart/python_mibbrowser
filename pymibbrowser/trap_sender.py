"""Send SNMP v1/v2c traps & notifications."""
from __future__ import annotations

import asyncio
import time

from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData, ContextData, NotificationType, ObjectIdentity, ObjectType,
    SnmpEngine, UdpTransportTarget, send_notification,
)
from pysnmp.proto import rfc1902


async def _send(host: str, port: int, community: str, version: str,
                trap_oid: str, var_binds: list[tuple[str, object]]) -> str:
    engine = SnmpEngine()
    target = await UdpTransportTarget.create((host, port), timeout=3, retries=0)
    mp = 0 if version == "1" else 1
    notif = NotificationType(ObjectIdentity(trap_oid))
    obj_types = [ObjectType(ObjectIdentity(oid), val) for oid, val in var_binds]
    try:
        err_ind, err_stat, err_idx, _ = await send_notification(
            engine, CommunityData(community, mpModel=mp), target,
            ContextData(), "trap", notif, *obj_types,
        )
        if err_ind:
            return f"error: {err_ind}"
        if err_stat:
            return f"status: {err_stat.prettyPrint()}"
        return "OK"
    finally:
        engine.close_dispatcher()


def send_trap(host: str, port: int, community: str, version: str,
              trap_oid: str, var_binds: list[tuple[str, object]]) -> str:
    return asyncio.run(_send(host, port, community, version, trap_oid, var_binds))
