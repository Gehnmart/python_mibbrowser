"""
Minimal SNMP v1/v2c agent simulator.

Backed by a simple OID → value dict that can be loaded from a snmpwalk-style
text file (`.1.3.6.1.2.1.1.1.0 = STRING: Linux ...` format, either iReasoning
export or net-snmp `snmpwalk` output).

Responds to GET / GETNEXT. GETBULK is handled as repeated GETNEXTs so the
behavior matches a real agent for scalar/column reads.
"""
from __future__ import annotations

import logging
import re
import socket
import threading
from dataclasses import dataclass

from pyasn1.codec.ber import decoder, encoder
from pysnmp.proto import api as snmp_api
from pysnmp.proto import rfc1905
from pysnmp.proto.rfc1902 import (
    Counter32,
    Counter64,
    Gauge32,
    Integer,
    IpAddress,
    ObjectIdentifier,
    OctetString,
    TimeTicks,
    Unsigned32,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Simple data store
# ---------------------------------------------------------------------------

_TYPE_KEYWORDS = {
    "INTEGER":    Integer,
    "Integer32":  Integer,
    "Gauge32":    Gauge32,
    "Counter32":  Counter32,
    "Counter64":  Counter64,
    "TimeTicks":  TimeTicks,
    "IpAddress":  IpAddress,
    "STRING":     OctetString,
    "OctetString":OctetString,
    "Hex-STRING": OctetString,
    "OID":        ObjectIdentifier,
    "OBJECT":     ObjectIdentifier,
    "Unsigned32": Unsigned32,
}


def _coerce(type_kw: str, raw_val: str):
    t = _TYPE_KEYWORDS.get(type_kw, OctetString)
    s = raw_val.strip()
    if t is OctetString:
        if s.startswith('"') and s.endswith('"'):
            s = s[1:-1]
        if type_kw.startswith("Hex"):
            try:
                return OctetString(hexValue=s.replace(" ", ""))
            except Exception:
                pass
        return OctetString(s)
    if t is ObjectIdentifier:
        s = s.lstrip(".")
        return ObjectIdentifier(s)
    if t is IpAddress:
        return IpAddress(s)
    if t is TimeTicks:
        # Often printed as "(123) 0:00:01.23" — take the int in parentheses.
        m = re.match(r"\(?(\d+)\)?", s)
        return TimeTicks(int(m.group(1)) if m else 0)
    # Generic numeric
    try:
        return t(int(s))
    except Exception:
        try:
            return t(s)
        except Exception:
            return OctetString(s)


def load_snmpwalk(path: str) -> dict[tuple[int, ...], object]:
    """Parse net-snmp/iReasoning snmpwalk-style file. Return OID tuple → value."""
    items: dict[tuple[int, ...], object] = {}
    pat = re.compile(r"^\s*\.?([\d.]+)\s*=\s*(?:([A-Za-z0-9-]+):\s*)?(.*)$")
    current_oid: tuple[int, ...] | None = None
    current_type: str = "STRING"
    current_val: list[str] = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = pat.match(line)
            if m:
                if current_oid is not None:
                    items[current_oid] = _coerce(current_type, " ".join(current_val))
                current_oid = tuple(int(p) for p in m.group(1).split("."))
                current_type = m.group(2) or "STRING"
                current_val = [m.group(3)]
            else:
                if current_oid is not None:
                    current_val.append(line.rstrip("\n"))
        if current_oid is not None:
            items[current_oid] = _coerce(current_type, " ".join(current_val))
    return items


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

@dataclass
class _Vb:
    oid: tuple[int, ...]
    value: object


class SnmpAgentSim:
    def __init__(self, port: int = 1161, community: str = "public") -> None:
        self.port = port
        self.community = community
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._data: dict[tuple[int, ...], object] = {}
        self._sorted_keys: list[tuple[int, ...]] = []

    # Data management -------------------------------------------------

    def set_data(self, items: dict[tuple[int, ...], object]) -> None:
        with self._lock:
            self._data = dict(items)
            self._sorted_keys = sorted(self._data.keys())

    def load_from_walk(self, path: str) -> int:
        items = load_snmpwalk(path)
        self.set_data(items)
        return len(items)

    # Lifecycle -------------------------------------------------------

    def start(self, bind_host: str = "0.0.0.0") -> None:
        if self._thread and self._thread.is_alive():
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((bind_host, self.port))
        self._sock.settimeout(0.5)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log.info("simulator listening on %s:%d (community=%s) with %d OIDs",
                 bind_host, self.port, self.community, len(self._data))

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=2)
        self._sock = None
        self._thread = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # Handlers --------------------------------------------------------

    def _get(self, oid: tuple[int, ...]) -> object | None:
        return self._data.get(oid)

    def _next(self, oid: tuple[int, ...]) -> tuple[tuple[int, ...], object] | None:
        # Find smallest key strictly greater.
        import bisect
        i = bisect.bisect_right(self._sorted_keys, oid)
        if i >= len(self._sorted_keys):
            return None
        k = self._sorted_keys[i]
        return k, self._data[k]

    def _run(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                reply = self._handle(data)
                if reply is not None:
                    self._sock.sendto(reply, addr)
            except Exception:
                log.exception("handler error")

    def _handle(self, data: bytes) -> bytes | None:
        # Try v2c first (spec is superset of v1 PDUs); fall back to v1. v1's
        # Message spec lacks SNMPv2 PDU tags (6/7/8) and would raise.
        proto_mod = snmp_api.PROTOCOL_MODULES[snmp_api.SNMP_VERSION_2C]
        try:
            msg, _ = decoder.decode(data, asn1Spec=proto_mod.Message())
        except Exception:
            proto_mod = snmp_api.PROTOCOL_MODULES[snmp_api.SNMP_VERSION_1]
            msg, _ = decoder.decode(data, asn1Spec=proto_mod.Message())
        version_int = int(msg.getComponentByPosition(0))
        community = str(msg.getComponentByPosition(1))
        if community != self.community:
            return None
        pdu = proto_mod.apiMessage.get_pdu(msg)
        pdu_type = pdu.getTagSet()

        var_binds_in = list(proto_mod.apiPDU.get_varbind_list(pdu))

        response_pdu = proto_mod.GetResponsePDU()
        proto_mod.apiPDU.set_defaults(response_pdu)
        proto_mod.apiPDU.set_request_id(response_pdu,
                                        proto_mod.apiPDU.get_request_id(pdu))

        resp_vbs = []
        err_stat = 0
        err_idx = 0

        if pdu_type == rfc1905.GetRequestPDU.tagSet:
            for i, vb in enumerate(var_binds_in, start=1):
                oid_obj, _ = proto_mod.apiVarBind.get_oid_value(vb)
                oid_t = tuple(int(x) for x in oid_obj.asTuple())
                with self._lock:
                    val = self._get(oid_t)
                if val is None:
                    if version_int == 0:
                        err_stat, err_idx = 2, i
                    val = rfc1905.NoSuchInstance("")
                resp_vbs.append((oid_obj, val))
        elif pdu_type == rfc1905.GetNextRequestPDU.tagSet:
            for i, vb in enumerate(var_binds_in, start=1):
                oid_obj, _ = proto_mod.apiVarBind.get_oid_value(vb)
                oid_t = tuple(int(x) for x in oid_obj.asTuple())
                with self._lock:
                    nxt = self._next(oid_t)
                if nxt is None:
                    if version_int == 0:
                        err_stat, err_idx = 2, i
                    resp_vbs.append((oid_obj, rfc1905.EndOfMibView("")))
                else:
                    k, v = nxt
                    resp_vbs.append((ObjectIdentifier(".".join(str(p) for p in k)), v))
        elif version_int != 0 and pdu_type == rfc1905.GetBulkRequestPDU.tagSet:
            non_rep = int(proto_mod.apiBulkPDU.get_non_repeaters(pdu))
            max_rep = int(proto_mod.apiBulkPDU.get_max_repetitions(pdu))
            for vb in var_binds_in[:non_rep]:
                oid_obj, _ = proto_mod.apiVarBind.get_oid_value(vb)
                oid_t = tuple(int(x) for x in oid_obj.asTuple())
                with self._lock:
                    nxt = self._next(oid_t)
                if nxt is None:
                    resp_vbs.append((oid_obj, rfc1905.EndOfMibView("")))
                else:
                    k, v = nxt
                    resp_vbs.append((ObjectIdentifier(".".join(str(p) for p in k)), v))
            for vb in var_binds_in[non_rep:]:
                oid_obj, _ = proto_mod.apiVarBind.get_oid_value(vb)
                oid_t = tuple(int(x) for x in oid_obj.asTuple())
                for _rep in range(max_rep):
                    with self._lock:
                        nxt = self._next(oid_t)
                    if nxt is None:
                        resp_vbs.append((ObjectIdentifier(".".join(str(p) for p in oid_t)),
                                         rfc1905.EndOfMibView("")))
                        break
                    k, v = nxt
                    resp_vbs.append((ObjectIdentifier(".".join(str(p) for p in k)), v))
                    oid_t = k
        elif pdu_type == rfc1905.SetRequestPDU.tagSet:
            for _i, vb in enumerate(var_binds_in, start=1):
                oid_obj, val = proto_mod.apiVarBind.get_oid_value(vb)
                oid_t = tuple(int(x) for x in oid_obj.asTuple())
                with self._lock:
                    self._data[oid_t] = val
                    if oid_t not in self._sorted_keys:
                        import bisect
                        bisect.insort(self._sorted_keys, oid_t)
                resp_vbs.append((oid_obj, val))
        else:
            return None

        out_vbl = proto_mod.VarBindList()
        for oid_obj, val in resp_vbs:
            vb = proto_mod.VarBind()
            proto_mod.apiVarBind.set_oid_value(vb, (oid_obj, val))
            out_vbl.append(vb)
        proto_mod.apiPDU.set_varbind_list(response_pdu, out_vbl)
        proto_mod.apiPDU.set_error_status(response_pdu, err_stat)
        proto_mod.apiPDU.set_error_index(response_pdu, err_idx)

        proto_mod.apiMessage.set_pdu(msg, response_pdu)
        return encoder.encode(msg)
