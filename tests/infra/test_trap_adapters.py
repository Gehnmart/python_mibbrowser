"""Trap adapter tests.

Publisher: stub trap_sender.send_trap, verify (oid, tag, raw) triples
encode through build_set_value before reaching pysnmp.

Subscription: end-to-end loopback — send a real UDP packet, confirm
the engine.TrapEvent surfaces with the expected fields stripped of
infra-only annotations."""
from __future__ import annotations

import socket
import time

import pytest
from pyasn1.codec.ber import encoder
from pysnmp.proto import api as snmp_api
from pysnmp.proto import rfc1902

from pymibbrowser.engine.model import TrapEvent
from pymibbrowser.infra import trap_sender
from pymibbrowser.infra.adapters import PysnmpTrapPublisher, UdpTrapSubscription
from pymibbrowser.infra.adapters.traps import _to_engine_event
from pymibbrowser.infra.trap_receiver import TrapEvent as RawTrapEvent


# --- Publisher ------------------------------------------------------------

class TestPysnmpTrapPublisher:
    @pytest.fixture
    def stub_send(self, monkeypatch):
        captured: dict = {}

        def fake_send(host, port, community, version, trap_oid, var_binds):
            captured["host"] = host
            captured["port"] = port
            captured["community"] = community
            captured["version"] = version
            captured["trap_oid"] = trap_oid
            captured["var_binds"] = var_binds
            return "OK"

        monkeypatch.setattr(trap_sender, "send_trap", fake_send)
        return captured

    def test_passes_host_port_community_through(self, stub_send):
        out = PysnmpTrapPublisher().send(
            "127.0.0.1", 162, "public", "2c",
            "1.3.6.1.4.1.99999.0.1", var_binds=[])
        assert out == "OK"
        assert stub_send["host"] == "127.0.0.1"
        assert stub_send["port"] == 162
        assert stub_send["community"] == "public"
        assert stub_send["version"] == "2c"
        assert stub_send["trap_oid"] == "1.3.6.1.4.1.99999.0.1"

    def test_encodes_each_triple_via_build_set_value(self, stub_send):
        PysnmpTrapPublisher().send(
            "127.0.0.1", 162, "public", "2c",
            "1.3.6.1.4.1.99999.0.1",
            var_binds=[
                ("1.3.6.1.4.1.99999.1", "i", "42"),
                ("1.3.6.1.4.1.99999.2", "s", "hello"),
            ])
        encoded = stub_send["var_binds"]
        assert len(encoded) == 2
        # First: integer
        oid_a, val_a = encoded[0]
        assert oid_a == "1.3.6.1.4.1.99999.1"
        assert isinstance(val_a, rfc1902.Integer32)
        assert int(val_a) == 42
        # Second: string
        oid_b, val_b = encoded[1]
        assert isinstance(val_b, rfc1902.OctetString)
        assert bytes(val_b) == b"hello"

    def test_no_per_instance_state(self):
        p = PysnmpTrapPublisher()
        assert p.__dict__ == {}


# --- _to_engine_event conversion -----------------------------------------

class TestEventConversion:
    def test_strips_severity_and_message(self):
        raw = RawTrapEvent(
            time=1234567890.5,
            source_ip="10.0.0.1",
            source_port=50001,
            version="2c",
            community="public",
            trap_oid="1.3.6.1.4.1.9999.0.1",
            uptime=42,
            severity="WARNING",      # populated by UI rules — must be dropped
            message="something bad",  # ditto
            var_binds=[("1.3.6.1.4.1.9999.7", "OctetString", "payload")],
            raw_bytes=b"\x00\x01\x02",
        )
        ev = _to_engine_event(raw)
        assert isinstance(ev, TrapEvent)
        assert ev.received_at == 1234567890.5
        assert ev.source_ip == "10.0.0.1"
        assert ev.trap_oid == "1.3.6.1.4.1.9999.0.1"
        assert ev.uptime == 42
        assert ev.var_binds == (
            ("1.3.6.1.4.1.9999.7", "OctetString", "payload"),)
        # severity / message are not part of engine.TrapEvent.
        assert not hasattr(ev, "severity")
        assert not hasattr(ev, "message")

    def test_v1_specific_fields_carry_through(self):
        raw = RawTrapEvent(
            time=0, source_ip="x", source_port=0, version="1",
            community="c", trap_oid="ent.0.7",
            enterprise="1.3.6.1.4.1.9999",
            generic_trap=6, specific_trap=7, agent_addr="1.2.3.4",
        )
        ev = _to_engine_event(raw)
        assert ev.version == "1"
        assert ev.enterprise == "1.3.6.1.4.1.9999"
        assert ev.generic_trap == 6
        assert ev.specific_trap == 7
        assert ev.agent_addr == "1.2.3.4"

    def test_engine_event_is_immutable(self):
        ev = _to_engine_event(RawTrapEvent(time=0, source_ip="x",
                                            source_port=0, version="2c",
                                            community="c", trap_oid=""))
        import dataclasses
        with pytest.raises(dataclasses.FrozenInstanceError):
            ev.community = "tampered"   # type: ignore[misc]


# --- Subscription end-to-end ---------------------------------------------

@pytest.fixture
def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _build_v2c_trap(community: str, trap_oid: str) -> bytes:
    proto = snmp_api.PROTOCOL_MODULES[snmp_api.SNMP_VERSION_2C]
    pdu = proto.SNMPv2TrapPDU()
    proto.apiTrapPDU.set_defaults(pdu)
    proto.apiTrapPDU.set_varbinds(pdu, [
        (rfc1902.ObjectName("1.3.6.1.2.1.1.3.0"), rfc1902.TimeTicks(99)),
        (rfc1902.ObjectName("1.3.6.1.6.3.1.1.4.1.0"),
         rfc1902.ObjectIdentifier(trap_oid)),
    ])
    msg = proto.Message()
    proto.apiMessage.set_defaults(msg)
    proto.apiMessage.set_community(msg, community)
    proto.apiMessage.set_pdu(msg, pdu)
    return encoder.encode(msg)


class TestSubscription:
    def test_lifecycle(self, free_port):
        received: list[TrapEvent] = []
        sub = UdpTrapSubscription(port=free_port, on_trap=received.append)
        assert not sub.is_running()
        sub.start()
        try:
            assert sub.is_running()
            sub.start()       # idempotent
        finally:
            sub.stop()
        assert not sub.is_running()
        sub.stop()            # idempotent

    def test_callback_receives_engine_event_end_to_end(self, free_port):
        received: list[TrapEvent] = []
        sub = UdpTrapSubscription(port=free_port, on_trap=received.append)
        sub.start()
        try:
            data = _build_v2c_trap("private", "1.3.6.1.4.1.9999.42.0.1")
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.sendto(data, ("127.0.0.1", free_port))
            s.close()
            deadline = time.monotonic() + 2.0
            while not received and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            sub.stop()
        assert received, "no trap surfaced"
        ev = received[0]
        # Surfaces as engine.TrapEvent — pure type, no severity/message.
        assert isinstance(ev, TrapEvent)
        assert ev.community == "private"
        assert ev.trap_oid == "1.3.6.1.4.1.9999.42.0.1"
        assert ev.source_ip == "127.0.0.1"
        # Adapter forwarded the raw bytes through.
        assert ev.raw_bytes == data

    def test_accept_from_filter_passes_through(self, free_port):
        """The accept_from arg is forwarded to the underlying listener
        — packets from non-allowed sources never reach the callback."""
        received: list[TrapEvent] = []
        sub = UdpTrapSubscription(
            port=free_port, on_trap=received.append,
            accept_from="192.168.99.99/32")
        sub.start()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.sendto(_build_v2c_trap("public", "1.3.6.1.4.1.9999.0.1"),
                     ("127.0.0.1", free_port))
            s.close()
            time.sleep(0.7)
        finally:
            sub.stop()
        assert received == []
