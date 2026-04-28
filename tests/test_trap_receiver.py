"""Trap receiver: accept-list filtering, parsing of v1/v2c traps,
listener lifecycle. Real UDP socket is used end-to-end against localhost
in one test (loopback only) — fast and self-contained."""
from __future__ import annotations

import socket
import time

import pytest
from pyasn1.codec.ber import encoder
from pysnmp.proto import api as snmp_api
from pysnmp.proto import rfc1902

from pymibbrowser.core import trap_receiver
from pymibbrowser.core.trap_receiver import TrapEvent, TrapListener, _pp


# --- accept-list ----------------------------------------------------------

class TestAcceptList:
    def test_empty_means_accept_any(self):
        l = TrapListener(accept_from="")
        assert l._allowed("1.2.3.4") is True
        assert l._allowed("203.0.113.1") is True

    def test_single_host(self):
        l = TrapListener(accept_from="10.1.2.3")
        assert l._allowed("10.1.2.3") is True
        assert l._allowed("10.1.2.4") is False

    def test_cidr(self):
        l = TrapListener(accept_from="10.0.0.0/8, 192.168.1.5")
        assert l._allowed("10.55.55.55") is True
        assert l._allowed("192.168.1.5") is True
        assert l._allowed("172.16.1.1") is False

    def test_invalid_token_logged_and_skipped(self, caplog):
        with caplog.at_level("WARNING"):
            l = TrapListener(accept_from="not-an-ip, 10.0.0.0/8")
        assert any("ignoring invalid" in r.message for r in caplog.records)
        # The valid token still applies.
        assert l._allowed("10.5.5.5") is True

    def test_invalid_source_ip_rejected(self):
        l = TrapListener(accept_from="10.0.0.0/8")
        assert l._allowed("not-an-ip") is False


# --- parsing v2c ----------------------------------------------------------

def _build_v2c_trap(community: str, trap_oid: str,
                    extra_vbs: list[tuple[str, object]] | None = None) -> bytes:
    """Encode a complete SNMPv2c trap message: sysUpTime + snmpTrapOID +
    user var-binds. Returns the on-the-wire BER bytes."""
    proto = snmp_api.PROTOCOL_MODULES[snmp_api.SNMP_VERSION_2C]
    pdu = proto.SNMPv2TrapPDU()
    proto.apiTrapPDU.set_defaults(pdu)

    var_binds = [
        (rfc1902.ObjectName("1.3.6.1.2.1.1.3.0"), rfc1902.TimeTicks(12345)),
        (rfc1902.ObjectName("1.3.6.1.6.3.1.1.4.1.0"),
         rfc1902.ObjectIdentifier(trap_oid)),
    ]
    for oid, val in (extra_vbs or []):
        var_binds.append((rfc1902.ObjectName(oid), val))
    proto.apiTrapPDU.set_varbinds(pdu, var_binds)

    msg = proto.Message()
    proto.apiMessage.set_defaults(msg)
    proto.apiMessage.set_community(msg, community)
    proto.apiMessage.set_pdu(msg, pdu)
    return encoder.encode(msg)


def _build_v1_trap(community: str, enterprise: str = "1.3.6.1.4.1.9999",
                   specific: int = 7, uptime: int = 4242) -> bytes:
    proto = snmp_api.PROTOCOL_MODULES[snmp_api.SNMP_VERSION_1]
    pdu = proto.TrapPDU()
    proto.apiTrapPDU.set_defaults(pdu)
    proto.apiTrapPDU.set_enterprise(pdu, rfc1902.ObjectIdentifier(enterprise))
    proto.apiTrapPDU.set_generic_trap(pdu, rfc1902.Integer(6))
    proto.apiTrapPDU.set_specific_trap(pdu, rfc1902.Integer(specific))
    proto.apiTrapPDU.set_timestamp(pdu, rfc1902.TimeTicks(uptime))
    proto.apiTrapPDU.set_varbinds(pdu, [
        (rfc1902.ObjectName("1.3.6.1.4.1.9999.1"),
         rfc1902.OctetString(b"hello")),
    ])

    msg = proto.Message()
    proto.apiMessage.set_defaults(msg)
    proto.apiMessage.set_community(msg, community)
    proto.apiMessage.set_pdu(msg, pdu)
    return encoder.encode(msg)


def test_parse_v2c_extracts_trap_oid_and_uptime():
    listener = TrapListener()
    data = _build_v2c_trap("public", "1.3.6.1.4.1.9999.1.2.3",
                           extra_vbs=[("1.3.6.1.4.1.9999.7",
                                        rfc1902.OctetString(b"payload"))])
    ev = listener._parse(data, ("203.0.113.5", 50000))
    assert ev.version == "2c"
    assert ev.community == "public"
    assert ev.uptime == 12345
    assert ev.trap_oid == "1.3.6.1.4.1.9999.1.2.3"
    # The non-standard varbind survived.
    oids = [vb[0] for vb in ev.var_binds]
    assert "1.3.6.1.4.1.9999.7" in oids
    # Source address fields are taken from addr.
    assert ev.source_ip == "203.0.113.5"
    assert ev.source_port == 50000
    assert ev.raw_bytes == data


def test_parse_v1_extracts_enterprise_and_specific():
    listener = TrapListener()
    data = _build_v1_trap("publicv1")
    ev = listener._parse(data, ("10.0.0.1", 1234))
    assert ev.version == "1"
    assert ev.community == "publicv1"
    assert ev.enterprise == "1.3.6.1.4.1.9999"
    assert ev.specific_trap == 7
    assert ev.uptime == 4242
    # trap_oid synthesised: enterprise.0.specific
    assert ev.trap_oid == "1.3.6.1.4.1.9999.0.7"
    # Varbinds preserved.
    assert any(vb[0] == "1.3.6.1.4.1.9999.1" for vb in ev.var_binds)


def test_parse_swallows_pretty_print_oid():
    """OctetString containing UTF-8 should round-trip via _pp."""
    assert _pp(rfc1902.OctetString(b"hello")) == "hello"
    # Binary content renders as hex.
    assert _pp(rfc1902.OctetString(b"\x00\xff")) == "00 FF"
    # Non-OctetString uses prettyPrint.
    assert _pp(rfc1902.Integer32(42)).strip() == "42"


# --- listener lifecycle ---------------------------------------------------

@pytest.fixture
def free_port():
    """Bind a UDP socket to port 0, grab the assigned port, close it.
    Then hand the port number to the listener. Race-prone in theory; fine
    for tests."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_listener_start_stop_cycle(free_port):
    received: list[TrapEvent] = []
    l = TrapListener(port=free_port, on_trap=received.append)
    l.start()
    try:
        assert l.is_running()
        # Calling start a second time is a no-op (idempotent).
        l.start()
    finally:
        l.stop()
    assert not l.is_running()
    # stop() is also idempotent — calling it twice must not raise.
    l.stop()


def test_listener_receives_v2c_trap_end_to_end(free_port):
    """Send a real UDP packet to the listener and confirm it surfaces as
    a TrapEvent through the on_trap callback."""
    received: list[TrapEvent] = []
    l = TrapListener(port=free_port, on_trap=received.append)
    l.start()
    try:
        data = _build_v2c_trap("private", "1.3.6.1.4.1.9999.42.0.1")
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(data, ("127.0.0.1", free_port))
        s.close()
        # Listener loop wakes every 0.5 s; give it up to 2 s.
        deadline = time.monotonic() + 2.0
        while not received and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        l.stop()
    assert received, "no trap surfaced via callback"
    ev = received[0]
    assert ev.version == "2c"
    assert ev.community == "private"
    assert ev.trap_oid == "1.3.6.1.4.1.9999.42.0.1"
    assert ev.source_ip == "127.0.0.1"


def test_listener_drops_unmatched_source(free_port):
    """accept_from="192.168.99.99/32" → loopback packets are filtered out
    before parse, so the callback never fires."""
    received: list[TrapEvent] = []
    l = TrapListener(port=free_port, on_trap=received.append,
                      accept_from="192.168.99.99/32")
    l.start()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(_build_v2c_trap("public", "1.3.6.1.4.1.9999.0.1"),
                 ("127.0.0.1", free_port))
        s.close()
        time.sleep(0.7)
    finally:
        l.stop()
    assert received == []


def test_listener_swallows_garbage_packet(free_port):
    """A non-SNMP UDP datagram must not crash the loop or fire the callback."""
    received: list[TrapEvent] = []
    l = TrapListener(port=free_port, on_trap=received.append)
    l.start()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(b"not an snmp packet at all", ("127.0.0.1", free_port))
        s.close()
        time.sleep(0.7)
    finally:
        l.stop()
    assert received == []
    # Listener is still healthy after the bad packet.
    assert not l.is_running()


def test_listener_swallows_callback_exception(free_port):
    """If on_trap raises, the loop logs and keeps running."""
    calls = {"n": 0}

    def cb(ev):
        calls["n"] += 1
        raise RuntimeError("callback boom")

    l = TrapListener(port=free_port, on_trap=cb)
    l.start()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for _ in range(2):
            s.sendto(_build_v2c_trap("public", "1.3.6.1.4.1.9999.0.1"),
                     ("127.0.0.1", free_port))
        s.close()
        deadline = time.monotonic() + 2.0
        while calls["n"] < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        l.stop()
    # Both packets reached the callback even though the first raised.
    assert calls["n"] == 2


def test_listener_permission_error_for_low_port(monkeypatch):
    """Binding to a privileged port without privileges must surface a
    helpful PermissionError (covers the bind-error branch)."""

    class _StubSock:
        def __init__(self, *a, **kw): pass
        def setsockopt(self, *a, **kw): pass
        def bind(self, addr):
            raise PermissionError("simulated EACCES")
        def close(self): pass
        def settimeout(self, _t): pass

    monkeypatch.setattr(trap_receiver.socket, "socket",
                        lambda *a, **kw: _StubSock())
    l = TrapListener(port=162)
    with pytest.raises(PermissionError, match="needs root"):
        l.start()
    assert not l.is_running()
