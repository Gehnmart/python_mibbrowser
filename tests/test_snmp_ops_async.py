"""Drive the async/sync wrappers in snmp_ops by stubbing pysnmp's command
primitives. This exercises happy-path, error_indication, and error_status
branches and the table_walk row-stitching logic without any real network."""
from __future__ import annotations

import pytest
from pysnmp.proto import rfc1902

from pymibbrowser.infra import snmp_ops
from pymibbrowser.infra.config import Agent
from pymibbrowser.infra.snmp_ops import SnmpError


@pytest.fixture
def agent():
    return Agent(host="127.0.0.1", port=161, version="2c",
                  max_repetitions=4, retries=0, timeout_s=0.1)


@pytest.fixture
def fake_engine(monkeypatch):
    """Stub SnmpEngine + UdpTransportTarget so no socket is touched."""
    class _Engine:
        def close_dispatcher(self): pass

    class _Target:
        @classmethod
        async def create(cls, *_a, **_kw): return cls()

    monkeypatch.setattr(snmp_ops, "SnmpEngine", _Engine)
    monkeypatch.setattr(snmp_ops, "UdpTransportTarget", _Target)


def _vb(oid: str, val):
    name = rfc1902.ObjectName(oid)
    return name, val


def _stub_cmd(monkeypatch, attr: str, return_value=None, *, error_ind=None,
              error_stat=None):
    """Replace one of get_cmd/next_cmd/bulk_cmd/set_cmd with an async stub.

    return_value: list of (name, val) pairs (the var_binds)."""
    async def fake(*_a, **_kw):
        # err_stat needs to be a falsy-ish thing or have prettyPrint; emulate.
        class _Err:
            def __init__(self, msg): self._m = msg
            def __bool__(self): return True
            def prettyPrint(self): return self._m
        es_obj = _Err(error_stat) if error_stat else 0
        return error_ind, es_obj, 0, return_value or []
    monkeypatch.setattr(snmp_ops, attr, fake)


# --- async_get ------------------------------------------------------------

def test_op_get_happy_path(agent, fake_engine, monkeypatch):
    _stub_cmd(monkeypatch, "get_cmd", return_value=[
        _vb("1.3.6.1.2.1.1.3.0", rfc1902.TimeTicks(123)),
    ])
    out = snmp_ops.op_get(agent, ["1.3.6.1.2.1.1.3.0"])
    assert len(out) == 1
    assert out[0].oid == (1, 3, 6, 1, 2, 1, 1, 3, 0)
    assert out[0].type_name == "TimeTicks"


def test_op_get_error_indication_raises(agent, fake_engine, monkeypatch):
    _stub_cmd(monkeypatch, "get_cmd", error_ind="No SNMP response received")
    with pytest.raises(SnmpError, match="No SNMP response"):
        snmp_ops.op_get(agent, ["1.3.6.1.2.1.1.3.0"])


def test_op_get_error_status_raises(agent, fake_engine, monkeypatch):
    _stub_cmd(monkeypatch, "get_cmd", error_stat="noSuchName")
    with pytest.raises(SnmpError, match="noSuchName"):
        snmp_ops.op_get(agent, ["1.3.6.1.2.1.1.3.0"])


# --- async_next -----------------------------------------------------------

def test_op_next_happy_path(agent, fake_engine, monkeypatch):
    _stub_cmd(monkeypatch, "next_cmd", return_value=[
        _vb("1.3.6.1.2.1.1.4.0", rfc1902.OctetString(b"contact")),
    ])
    out = snmp_ops.op_next(agent, ["1.3.6.1.2.1.1.3.0"])
    assert out[0].oid[-2:] == (4, 0)


def test_op_next_error_indication(agent, fake_engine, monkeypatch):
    _stub_cmd(monkeypatch, "next_cmd", error_ind="timeout")
    with pytest.raises(SnmpError, match="timeout"):
        snmp_ops.op_next(agent, ["1.3.6.1.2.1.1.3.0"])


# --- async_bulk -----------------------------------------------------------

def test_op_bulk_happy_path(agent, fake_engine, monkeypatch):
    _stub_cmd(monkeypatch, "bulk_cmd", return_value=[
        _vb("1.3.6.1.2.1.1.4.0", rfc1902.OctetString(b"a")),
        _vb("1.3.6.1.2.1.1.5.0", rfc1902.OctetString(b"b")),
    ])
    out = snmp_ops.op_bulk(agent, ["1.3.6.1.2.1.1.3.0"])
    assert len(out) == 2


# --- async_set ------------------------------------------------------------

def test_op_set_happy_path(agent, fake_engine, monkeypatch):
    _stub_cmd(monkeypatch, "set_cmd", return_value=[
        _vb("1.3.6.1.2.1.1.4.0", rfc1902.OctetString(b"new")),
    ])
    pairs = [("1.3.6.1.2.1.1.4.0", rfc1902.OctetString(b"new"))]
    out = snmp_ops.op_set(agent, pairs)
    assert out[0].display_value == "new"


def test_op_set_error_status(agent, fake_engine, monkeypatch):
    _stub_cmd(monkeypatch, "set_cmd", error_stat="readOnly")
    with pytest.raises(SnmpError, match="readOnly"):
        snmp_ops.op_set(agent, [("1.3.6.1", rfc1902.OctetString(b"x"))])


# --- async_walk -----------------------------------------------------------

def test_op_walk_iterates_then_leaves_subtree(agent, fake_engine, monkeypatch):
    """Walk emits varbinds while we're inside the requested root and stops
    cleanly when the next OID is outside it."""
    rounds = iter([
        # First next_cmd call → still in subtree.
        [_vb("1.3.6.1.2.1.1.1.0", rfc1902.OctetString(b"a"))],
        # Second → still in subtree.
        [_vb("1.3.6.1.2.1.1.2.0", rfc1902.OctetString(b"b"))],
        # Third → escaped to sibling subtree, walk should stop.
        [_vb("1.3.6.1.2.1.2.1.0", rfc1902.OctetString(b"esc"))],
    ])

    async def fake_next(*_a, **_kw):
        return None, 0, 0, next(rounds)
    monkeypatch.setattr(snmp_ops, "next_cmd", fake_next)
    monkeypatch.setattr(snmp_ops, "is_end_of_mib", lambda _x: False)

    seen = []
    out = snmp_ops.op_walk(agent, ".1.3.6.1.2.1.1", cb=seen.append)
    assert [vb.oid for vb in out] == [
        (1, 3, 6, 1, 2, 1, 1, 1, 0),
        (1, 3, 6, 1, 2, 1, 1, 2, 0),
    ]
    # Progress callback fires for every emitted varbind.
    assert [vb.oid for vb in seen] == [vb.oid for vb in out]


def test_op_walk_stops_on_end_of_mib(agent, fake_engine, monkeypatch):
    async def fake_next(*_a, **_kw):
        return None, 0, 0, [_vb("1.3.6.1", rfc1902.OctetString(b"x"))]
    monkeypatch.setattr(snmp_ops, "next_cmd", fake_next)
    monkeypatch.setattr(snmp_ops, "is_end_of_mib", lambda _x: True)
    out = snmp_ops.op_walk(agent, ".1.3.6.1.2.1.1")
    assert out == []


def test_op_walk_empty_response_breaks(agent, fake_engine, monkeypatch):
    async def fake_next(*_a, **_kw):
        return None, 0, 0, []
    monkeypatch.setattr(snmp_ops, "next_cmd", fake_next)
    out = snmp_ops.op_walk(agent, ".1.3.6.1")
    assert out == []


def test_op_walk_error_indication(agent, fake_engine, monkeypatch):
    async def fake_next(*_a, **_kw):
        return "no response", 0, 0, []
    monkeypatch.setattr(snmp_ops, "next_cmd", fake_next)
    with pytest.raises(SnmpError, match="no response"):
        snmp_ops.op_walk(agent, ".1.3.6.1")


def test_op_walk_breaks_on_non_monotonic_oid(agent, fake_engine, monkeypatch,
                                                caplog):
    """A misbehaving agent that re-issues the same OID (or worse, walks
    backwards) used to spin forever — only the user killing the QThread
    stopped it. The monotonicity guard logs and breaks instead."""
    rounds = iter([
        [_vb("1.3.6.1.2.1.1.1.0", rfc1902.OctetString(b"a"))],
        [_vb("1.3.6.1.2.1.1.2.0", rfc1902.OctetString(b"b"))],
        # Agent echoes the previous OID — no progress.
        [_vb("1.3.6.1.2.1.1.2.0", rfc1902.OctetString(b"again"))],
        # If the guard didn't fire, we'd consume more — but the iter
        # is exhausted, so a missing guard would surface as
        # StopIteration instead of an infinite loop in this test.
    ])

    async def fake_next(*_a, **_kw):
        return None, 0, 0, next(rounds)
    monkeypatch.setattr(snmp_ops, "next_cmd", fake_next)
    monkeypatch.setattr(snmp_ops, "is_end_of_mib", lambda _x: False)

    with caplog.at_level("WARNING"):
        out = snmp_ops.op_walk(agent, ".1.3.6.1.2.1.1")
    # Walk stopped at the second result (the third was rejected).
    assert [vb.oid for vb in out] == [
        (1, 3, 6, 1, 2, 1, 1, 1, 0),
        (1, 3, 6, 1, 2, 1, 1, 2, 0),
    ]
    assert any("non-monotonic" in r.message for r in caplog.records)


def test_op_walk_breaks_on_oid_going_backwards(agent, fake_engine, monkeypatch):
    """Stricter case — strictly-smaller OID after a valid one. Same
    guard should catch it."""
    rounds = iter([
        [_vb("1.3.6.1.2.1.1.5.0", rfc1902.OctetString(b"first"))],
        # Goes backwards.
        [_vb("1.3.6.1.2.1.1.3.0", rfc1902.OctetString(b"back"))],
    ])

    async def fake_next(*_a, **_kw):
        return None, 0, 0, next(rounds)
    monkeypatch.setattr(snmp_ops, "next_cmd", fake_next)
    monkeypatch.setattr(snmp_ops, "is_end_of_mib", lambda _x: False)

    out = snmp_ops.op_walk(agent, ".1.3.6.1.2.1.1")
    assert [vb.oid for vb in out] == [(1, 3, 6, 1, 2, 1, 1, 5, 0)]


# --- async_table_walk -----------------------------------------------------

def test_table_walk_no_columns_returns_empty(agent, fake_engine):
    out = snmp_ops.op_table_walk(agent, [])
    assert out == []


def test_table_walk_stitches_two_columns(agent, fake_engine, monkeypatch):
    """One bulk_cmd round returns two rows × two columns; the second round
    reports both columns leaving the subtree (signals done)."""
    col_a = (1, 3, 6, 1, 2, 1, 2, 2, 1, 2)   # ifDescr
    col_b = (1, 3, 6, 1, 2, 1, 2, 2, 1, 3)   # ifType

    rounds = iter([
        # Round 1: col_a row1, col_b row1, col_a row2, col_b row2.
        [
            _vb("1.3.6.1.2.1.2.2.1.2.1", rfc1902.OctetString(b"eth0")),
            _vb("1.3.6.1.2.1.2.2.1.3.1", rfc1902.Integer32(6)),
            _vb("1.3.6.1.2.1.2.2.1.2.2", rfc1902.OctetString(b"eth1")),
            _vb("1.3.6.1.2.1.2.2.1.3.2", rfc1902.Integer32(6)),
        ],
        # Round 2: both columns left their subtree → walk completes.
        [
            _vb("1.3.6.1.2.1.2.2.1.4.1", rfc1902.Integer32(0)),
            _vb("1.3.6.1.2.1.2.2.1.5.1", rfc1902.Integer32(0)),
        ],
    ])

    async def fake_bulk(*_a, **_kw):
        return None, 0, 0, next(rounds)
    monkeypatch.setattr(snmp_ops, "bulk_cmd", fake_bulk)
    monkeypatch.setattr(snmp_ops, "is_end_of_mib", lambda _x: False)

    progress = []
    out = snmp_ops.op_table_walk(agent, [col_a, col_b], cb=progress.append)
    # Expect 2 rows × 2 cols = 4 varbinds, in repetition-major order.
    assert len(out) == 4
    assert {vb.oid[-2:] for vb in out} == {(2, 1), (3, 1), (2, 2), (3, 2)}
    assert len(progress) == 4


def test_table_walk_breaks_on_empty_response(agent, fake_engine, monkeypatch):
    async def fake_bulk(*_a, **_kw):
        return None, 0, 0, []
    monkeypatch.setattr(snmp_ops, "bulk_cmd", fake_bulk)
    out = snmp_ops.op_table_walk(agent, [(1, 3, 6, 1)])
    assert out == []


def test_table_walk_error_indication(agent, fake_engine, monkeypatch):
    async def fake_bulk(*_a, **_kw):
        return "timeout", 0, 0, []
    monkeypatch.setattr(snmp_ops, "bulk_cmd", fake_bulk)
    with pytest.raises(SnmpError, match="timeout"):
        snmp_ops.op_table_walk(agent, [(1, 3, 6, 1)])
