"""Standalone SNMP trap listener (UDP). Emits parsed trap events via callback."""
from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from pyasn1.codec.ber import decoder
from pysnmp.proto import api as snmp_api
from pysnmp.proto import rfc1902, rfc1905

log = logging.getLogger(__name__)


@dataclass
class TrapEvent:
    time: float
    source_ip: str
    source_port: int
    version: str               # "1" | "2c"
    community: str
    trap_oid: str              # string OID
    uptime: int = 0
    enterprise: str = ""
    generic_trap: int = 0
    specific_trap: int = 0
    agent_addr: str = ""
    severity: str = "INFO"     # filled by rules
    message: str = ""          # description via rules
    var_binds: list[tuple[str, str, str]] = field(default_factory=list)
    # ^ (oid, type, value-as-string)
    raw_bytes: bytes = b""        # original UDP payload — for hex dump


class TrapListener:
    """Threaded UDP listener on a given port."""
    def __init__(self, port: int = 162,
                 on_trap: Optional[Callable[[TrapEvent], None]] = None,
                 accept_from: str = "") -> None:
        self.port = port
        self._on_trap = on_trap
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        # Parse the accept-list once — we match every incoming datagram
        # against it inside the hot loop, so we want cheap
        # ipaddress.IPv4Network objects, not strings.
        self._accept_nets: list = []
        for token in (accept_from or "").split(","):
            t = token.strip()
            if not t:
                continue
            try:
                import ipaddress
                self._accept_nets.append(
                    ipaddress.ip_network(t, strict=False))
            except ValueError:
                log.warning("trap accept_from: ignoring invalid %r", t)

    def _allowed(self, ip: str) -> bool:
        if not self._accept_nets:
            return True
        try:
            import ipaddress
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in net for net in self._accept_nets)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Allow rebinding while previous socket still in TIME_WAIT.
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", self.port))
        except PermissionError as exc:
            self._sock.close()
            self._sock = None
            raise PermissionError(
                f"Cannot bind port {self.port} (needs root for <1024)") from exc
        self._sock.settimeout(0.5)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log.info("Trap listener started on 0.0.0.0:%d", self.port)

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._sock = None
        self._thread = None
        log.info("Trap listener stopped")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            if not self._allowed(addr[0]):
                # Dropped before parse — no CPU burn on spoofed/unsolicited
                # datagrams.
                continue
            try:
                ev = self._parse(data, addr)
            except Exception as exc:
                log.warning("Failed to parse trap from %s: %s", addr, exc)
                continue
            if ev and self._on_trap:
                try:
                    self._on_trap(ev)
                except Exception:
                    log.exception("trap callback error")

    def _parse(self, data: bytes, addr: tuple[str, int]) -> Optional[TrapEvent]:
        """Decode an SNMP trap packet (v1 or v2c) into a TrapEvent."""
        # Peek version by trying v2c first (superset); if that fails fall back
        # to v1. Decoding a v2c SNMPv2TrapPDU (tag 7) with v1's Message spec
        # raises because v1 only knows tags 0..4 in the PDU union.
        proto_mod = snmp_api.PROTOCOL_MODULES[snmp_api.SNMP_VERSION_2C]
        try:
            msg, _ = decoder.decode(data, asn1Spec=proto_mod.Message())
        except Exception:
            proto_mod = snmp_api.PROTOCOL_MODULES[snmp_api.SNMP_VERSION_1]
            msg, _ = decoder.decode(data, asn1Spec=proto_mod.Message())
        version_int = int(msg.getComponentByPosition(0))
        community = str(msg.getComponentByPosition(1))
        pdu = proto_mod.apiMessage.get_pdu(msg)

        ev = TrapEvent(
            time=time.time(),
            source_ip=addr[0],
            source_port=addr[1],
            version="1" if version_int == 0 else "2c",
            community=community,
            trap_oid="",
            raw_bytes=data,
        )

        if version_int == 0:
            ev.enterprise = str(proto_mod.apiTrapPDU.get_enterprise(pdu))
            ev.agent_addr = str(proto_mod.apiTrapPDU.get_agent_address(pdu))
            ev.generic_trap = int(proto_mod.apiTrapPDU.get_generic_trap(pdu))
            ev.specific_trap = int(proto_mod.apiTrapPDU.get_specific_trap(pdu))
            ev.uptime = int(proto_mod.apiTrapPDU.get_timestamp(pdu))
            ev.trap_oid = f"{ev.enterprise}.0.{ev.specific_trap}"
            for vb in proto_mod.apiTrapPDU.get_varbind_list(pdu):
                oid, val = proto_mod.apiVarBind.get_oid_value(vb)
                ev.var_binds.append((str(oid), val.__class__.__name__, _pp(val)))
        else:
            for vb in proto_mod.apiPDU.get_varbind_list(pdu):
                oid, val = proto_mod.apiVarBind.get_oid_value(vb)
                oid_s = str(oid)
                if oid_s == "1.3.6.1.2.1.1.3.0":
                    try:
                        ev.uptime = int(val)
                    except Exception:
                        pass
                elif oid_s == "1.3.6.1.6.3.1.1.4.1.0":
                    ev.trap_oid = str(val)
                else:
                    ev.var_binds.append((oid_s, val.__class__.__name__, _pp(val)))
        return ev


def _pp(val) -> str:
    try:
        if isinstance(val, rfc1902.OctetString):
            raw = bytes(val.asOctets()) if hasattr(val, "asOctets") else bytes(val)
            if all(32 <= b < 127 or b in (9, 10, 13) for b in raw):
                try:
                    return raw.decode("utf-8")
                except UnicodeDecodeError:
                    pass
            return " ".join(f"{b:02X}" for b in raw)
        return val.prettyPrint()
    except Exception:
        return str(val)
