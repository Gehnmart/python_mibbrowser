"""trap_sender wraps pysnmp's async send_notification. We stub the network
edge and verify the version→mpModel mapping plus the three return paths
(OK / error_indication / error_status)."""
from __future__ import annotations

import pytest

from pymibbrowser.infra import trap_sender


@pytest.fixture
def fake_engine(monkeypatch):
    class _Engine:
        def close_dispatcher(self): pass

    class _Target:
        @classmethod
        async def create(cls, *_a, **_kw): return cls()

    monkeypatch.setattr(trap_sender, "SnmpEngine", _Engine)
    monkeypatch.setattr(trap_sender, "UdpTransportTarget", _Target)


def _stub_send(monkeypatch, *, err_ind=None, err_stat=None):
    captured: dict = {}

    class _ErrStat:
        def __init__(self, m): self._m = m
        def __bool__(self): return True
        def prettyPrint(self): return self._m

    async def fake_send(engine, auth, target, ctx, _kind, notif, *obj_types):
        captured["auth"] = auth
        captured["kind"] = _kind
        captured["notif"] = notif
        captured["nvars"] = len(obj_types)
        es = _ErrStat(err_stat) if err_stat else 0
        return err_ind, es, 0, []

    monkeypatch.setattr(trap_sender, "send_notification", fake_send)
    return captured


def test_send_trap_v2c_ok(fake_engine, monkeypatch):
    seen = _stub_send(monkeypatch)
    out = trap_sender.send_trap(
        "127.0.0.1", 162, "public", "2c", "1.3.6.1.4.1.9999.0.1",
        var_binds=[("1.3.6.1.4.1.9999.7", b"payload")],
    )
    assert out == "OK"
    # Two var_binds were sent (the user one); the trap_oid itself is
    # carried via NotificationType, not as an ObjectType.
    assert seen["nvars"] == 1
    # mpModel for v2c is 1; v1 is 0.
    assert seen["auth"].message_processing_model == 1


def test_send_trap_v1_uses_mp_model_zero(fake_engine, monkeypatch):
    seen = _stub_send(monkeypatch)
    trap_sender.send_trap("127.0.0.1", 162, "public", "1",
                           "1.3.6.1.4.1.9999.0.1", var_binds=[])
    assert seen["auth"].message_processing_model == 0
    assert seen["nvars"] == 0


def test_send_trap_error_indication_returned_as_string(fake_engine, monkeypatch):
    _stub_send(monkeypatch, err_ind="No SNMP response received")
    out = trap_sender.send_trap("127.0.0.1", 162, "public", "2c",
                                  "1.3.6.1.4.1.9999.0.1", var_binds=[])
    assert out.startswith("error: No SNMP response")


def test_send_trap_error_status_returned_as_string(fake_engine, monkeypatch):
    _stub_send(monkeypatch, err_stat="genErr")
    out = trap_sender.send_trap("127.0.0.1", 162, "public", "2c",
                                  "1.3.6.1.4.1.9999.0.1", var_binds=[])
    assert out == "status: genErr"
