"""Cover the pure-logic helpers in snmp_ops without touching the network.

The async_* functions go through pysnmp's dispatcher and require either real
agents or heavy mocking; those live in tests/test_snmp_ops_async.py."""
from __future__ import annotations

import pytest
from pysnmp.proto import rfc1902

from pymibbrowser.infra import snmp_ops
from pymibbrowser.infra.config import Agent
from pymibbrowser.infra.snmp_ops import (
    SnmpError,
    VarBind,
    _build_auth,
    _display,
    _format_timeticks,
    _parse_oid,
    build_set_value,
)

# --- _format_timeticks ----------------------------------------------------

class TestFormatTimeticks:
    def test_seconds_only(self):
        # 1234 hundredths = 12.34 s
        assert _format_timeticks(1234) == "12.34 seconds (1234)"

    def test_zero(self):
        assert _format_timeticks(0) == "0.00 seconds (0)"

    def test_one_minute(self):
        # 6000 hundredths = 1 min
        assert _format_timeticks(6000).startswith("1 minute 0.00 seconds")

    def test_minutes_plural(self):
        assert _format_timeticks(12_000).startswith("2 minutes")

    def test_one_hour(self):
        assert _format_timeticks(360_000).startswith("1 hour 0.00 seconds")

    def test_hours_plural(self):
        assert _format_timeticks(720_000).startswith("2 hours")

    def test_one_day(self):
        assert _format_timeticks(8_640_000).startswith("1 day 0.00 seconds")

    def test_multiple_days(self):
        assert _format_timeticks(2 * 8_640_000).startswith("2 days")

    def test_full_breakdown(self):
        # 1d 2h 3m 4.50s = 8_640_000 + 720_000 + 18_000 + 450 = 9_378_450
        assert _format_timeticks(9_378_450) == \
            "1 day 2 hours 3 minutes 4.50 seconds (9378450)"

    def test_negative_passes_through(self):
        assert _format_timeticks(-5) == "-5"


# --- _display -------------------------------------------------------------

class TestDisplay:
    def test_timeticks(self):
        out = _display(rfc1902.TimeTicks(6000))
        assert "1 minute" in out
        assert "(6000)" in out

    def test_ipaddress_4bytes(self):
        ip = rfc1902.IpAddress("10.20.30.40")
        assert _display(ip) == "10.20.30.40"

    def test_octetstring_ascii(self):
        assert _display(rfc1902.OctetString(b"hello")) == "hello"

    def test_octetstring_utf8(self):
        assert _display(rfc1902.OctetString("привет".encode())) != ""

    def test_octetstring_binary_renders_hex(self):
        out = _display(rfc1902.OctetString(b"\x00\xff\x01"))
        assert out == "00 FF 01"

    def test_object_identifier(self):
        out = _display(rfc1902.ObjectIdentifier("1.3.6.1.2.1.1.3.0"))
        assert out == ".1.3.6.1.2.1.1.3.0"

    def test_integer_uses_pretty_print(self):
        out = _display(rfc1902.Integer32(42))
        assert "42" in out

    def test_display_swallows_errors(self):
        """Anything raising must downgrade to str(val) instead of bubbling up."""
        class Boom:
            def prettyPrint(self):
                raise RuntimeError("x")

            def __str__(self):  # str() must work
                return "<boom>"
        assert _display(Boom()) == "<boom>"


# --- _parse_oid -----------------------------------------------------------

class TestParseOid:
    def test_dotted_numeric(self):
        # _parse_oid returns an ObjectIdentity; resolving it requires a MIB
        # controller. We just exercise the function and check it doesn't
        # raise for a plain numeric.
        _parse_oid("1.3.6.1.2.1.1.3.0")
        _parse_oid(".1.3.6.1.2.1.1.3.0")

    def test_iterable_of_ints(self):
        _parse_oid([1, 3, 6, 1, 2, 1, 1, 3, 0])
        _parse_oid((1, 3, 6))

    def test_symbolic(self):
        # No exception — symbolic resolution is deferred to MibViewController.
        _parse_oid("sysUpTime.0")


# --- VarBind.from_pysnmp --------------------------------------------------

class TestVarBindFromPysnmp:
    def test_extracts_oid_tuple_and_type(self):
        name = rfc1902.ObjectName("1.3.6.1.2.1.1.3.0")
        val = rfc1902.TimeTicks(123)
        vb = VarBind.from_pysnmp(name, val)
        assert vb.oid == (1, 3, 6, 1, 2, 1, 1, 3, 0)
        assert vb.type_name == "TimeTicks"
        assert "1.23 seconds" in vb.display_value


# --- _build_auth ----------------------------------------------------------

class TestBuildAuth:
    def test_v2c_read(self):
        a = Agent(version="2c", read_community="public",
                   write_community="private")
        cd = _build_auth(a)
        # CommunityData masks .communityName in repr; compare via str().
        assert str(cd.communityName) == "public"
        # message_processing_model 1 == SNMPv2c
        assert cd.message_processing_model == 1

    def test_v2c_write_uses_write_community(self):
        a = Agent(version="2c", read_community="public",
                   write_community="secret")
        cd = _build_auth(a, for_write=True)
        assert str(cd.communityName) == "secret"

    def test_v1(self):
        a = Agent(version="1", read_community="public")
        cd = _build_auth(a)
        assert cd.message_processing_model == 0

    def test_v3_unknown_protocols_fall_back_to_none(self):
        a = Agent(version="3", user="u",
                   auth_protocol="bogus", priv_protocol="bogus")
        usm = _build_auth(a)
        # Known fact: usmNoAuthProtocol/usmNoPrivProtocol are the fallback.
        assert usm.userName == "u"

    def test_v3_known_protocols(self):
        a = Agent(version="3", user="u",
                   auth_protocol="sha", auth_password="pw",
                   priv_protocol="aes256", priv_password="pw2")
        usm = _build_auth(a)
        assert usm.userName == "u"

    def test_v3_priv_without_auth_raises(self):
        """SNMPv3 spec forbids noAuthPriv — privacy keys are derived
        from the auth credential, so encryption-without-auth has no
        meaningful key. pysnmp surfaces this as an opaque internal
        error; we want a clear, actionable diagnostic."""
        a = Agent(version="3", user="u",
                   auth_protocol="none",
                   priv_protocol="aes",
                   priv_password="pw")
        with pytest.raises(SnmpError, match="privacy requires authentication"):
            _build_auth(a)

    def test_v3_no_auth_no_priv_ok(self):
        """noAuthNoPriv is the legitimate degenerate case — used for
        device discovery, public OIDs."""
        a = Agent(version="3", user="u",
                   auth_protocol="none", priv_protocol="none")
        usm = _build_auth(a)
        assert usm.userName == "u"


# --- build_set_value ------------------------------------------------------

class TestBuildSetValue:
    def test_integer(self):
        v = build_set_value("i", "42")
        assert isinstance(v, rfc1902.Integer32)
        assert int(v) == 42

    def test_unsigned(self):
        v = build_set_value("u", "100")
        assert isinstance(v, rfc1902.Unsigned32)

    def test_timeticks(self):
        v = build_set_value("t", "1000")
        assert isinstance(v, rfc1902.TimeTicks)

    def test_ipaddress(self):
        v = build_set_value("a", "10.0.0.1")
        assert isinstance(v, rfc1902.IpAddress)

    def test_oid_tag_strips_leading_dot(self):
        v = build_set_value("o", ".1.3.6.1")
        assert isinstance(v, rfc1902.ObjectName)

    def test_string_default(self):
        v = build_set_value("s", "hello")
        assert isinstance(v, rfc1902.OctetString)
        assert bytes(v) == b"hello"

    def test_hex_with_spaces(self):
        v = build_set_value("x", "DE AD BE EF")
        assert isinstance(v, rfc1902.OctetString)
        assert bytes(v) == b"\xde\xad\xbe\xef"

    def test_hex_with_0x_prefix(self):
        v = build_set_value("x", "0xDEAD")
        assert bytes(v) == b"\xde\xad"

    def test_counter_and_gauge(self):
        assert isinstance(build_set_value("c", "1"), rfc1902.Counter32)
        assert isinstance(build_set_value("g", "1"), rfc1902.Gauge32)

    def test_unknown_tag_falls_back_to_string(self):
        # Empty/None tag → 's' default branch.
        v = build_set_value("", "x")
        assert isinstance(v, rfc1902.OctetString)
        v = build_set_value("z", "x")    # unknown tag
        assert isinstance(v, rfc1902.OctetString)

    def test_uppercase_tag_works(self):
        # Tag is case-insensitive.
        v = build_set_value("I", "42")
        assert isinstance(v, rfc1902.Integer32)

    def test_bad_value_for_integer_raises_snmp_error(self):
        """Passing 'abc' for type tag 'i' used to bubble out a pyasn1
        ValueConstraintError three frames deep. Now wrapped as
        SnmpError so callers see the offending value and tag."""
        with pytest.raises(SnmpError, match="bad value 'abc' for type tag 'i'"):
            build_set_value("i", "abc")

    def test_bad_hex_value_raises_snmp_error(self):
        """Same wrapping for the hex-tag path."""
        with pytest.raises(SnmpError, match="bad value 'not-hex'"):
            build_set_value("x", "not-hex")

    def test_oversized_integer_raises_snmp_error(self):
        """pyasn1's range constraint on Integer32 (signed 32-bit) —
        anything larger comes back wrapped, not as a raw pyasn1 error."""
        with pytest.raises(SnmpError, match="bad value"):
            build_set_value("i", "99999999999999999999")


# --- SnmpError ------------------------------------------------------------

def test_snmp_error_str():
    e = SnmpError("boom")
    assert str(e) == "boom"


# --- _run -----------------------------------------------------------------

class TestRun:
    def test_runs_simple_coroutine(self):
        async def f():
            return 42
        assert snmp_ops._run(f()) == 42

    def test_propagates_unrelated_runtime_errors(self):
        async def f():
            raise RuntimeError("not the loop one")
        with pytest.raises(RuntimeError, match="not the loop one"):
            snmp_ops._run(f())

    def test_loop_persists_across_calls(self):
        """The whole point of the runner: one loop, many submissions.
        After two consecutive _run() calls the loop thread is still
        the same — no per-call create/teardown."""
        async def thread_id():
            import threading
            return threading.current_thread().ident
        first = snmp_ops._run(thread_id())
        second = snmp_ops._run(thread_id())
        assert first == second
        # And neither was the calling thread.
        import threading as _t
        assert first != _t.current_thread().ident

    def test_shutdown_is_idempotent(self):
        """Tests / process-cleanup paths may call shutdown() more than
        once; second+ call must be a no-op, not a crash."""
        runner = snmp_ops._LoopRunner()
        # Never started — shutdown is a no-op.
        runner.shutdown()
        # Start, shutdown, shutdown again.
        async def f():
            return 1
        assert runner.submit(f()) == 1
        runner.shutdown()
        runner.shutdown()

    def test_shutdown_cancels_pending_tasks(self):
        """A coroutine left running on the loop (e.g. a stuck pysnmp
        next_cmd that the calling QThread already abandoned) used to
        keep the loop's selector half-alive — manifests as
        'never awaited' RuntimeWarnings and leaked socket FDs in long
        test sessions. shutdown() now cancels every pending task
        before stopping the loop, so the loop thread joins cleanly
        even with abandoned work in flight."""
        import asyncio
        import time
        runner = snmp_ops._LoopRunner()
        runner._ensure_started()
        # Schedule a coroutine that would never finish on its own.
        async def stuck():
            await asyncio.sleep(60)
        assert runner._loop is not None
        asyncio.run_coroutine_threadsafe(stuck(), runner._loop)
        # Give the loop a moment to actually start the task.
        time.sleep(0.05)
        t0 = time.monotonic()
        runner.shutdown()
        # Without the cancel-before-stop path, shutdown would either
        # hang on join(timeout=2.0) or return at exactly the timeout.
        # With it, the loop unwinds the cancelled task and exits
        # run_forever well under that budget.
        assert time.monotonic() - t0 < 1.0
        # And the loop thread is gone.
        assert runner._thread is None

    def test_submit_from_loop_thread_raises(self):
        """A coroutine running on the loop must not call _run() — that
        would block the loop on a future of its own work. We reject
        the call rather than deadlock silently."""
        runner = snmp_ops._LoopRunner()
        try:
            async def _noop():
                return None
            inner = _noop()
            try:
                async def reentrant():
                    runner.submit(inner)
                with pytest.raises(RuntimeError, match="loop thread"):
                    runner.submit(reentrant())
            finally:
                # submit() rejected before scheduling, so close the
                # never-awaited coroutine explicitly to avoid
                # RuntimeWarning at GC time.
                inner.close()
        finally:
            runner.shutdown()
