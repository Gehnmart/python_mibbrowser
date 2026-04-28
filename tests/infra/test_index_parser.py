"""Index-suffix decomposition for SNMP table rows.

The parser used to live as a ~80-LOC method on TableViewTab — Qt-bound,
no tests. After extraction it's pure: tree lookup + arithmetic. These
tests double as the spec for "what does an OID suffix actually mean
on a real table?"

Each scenario uses the exact suffix you'd see walking that table on
a live device, so a regression here matches a regression a user
would actually notice.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pymibbrowser.infra.index_parser import (
    parse_index_suffix,
    render_octet_index,
)

# --- minimal stubs --------------------------------------------------------

@dataclass
class _Node:
    """Stand-in for MibNode — only the fields parse_index_suffix reads."""
    syntax: str = ""
    enum_values: dict[int, str] = field(default_factory=dict)


class _Tree:
    def __init__(self, **nodes: _Node) -> None:
        self._n = nodes

    def node_by_name(self, name: str) -> _Node | None:
        return self._n.get(name)


# --- single-index tables --------------------------------------------------

class TestIntegerIndex:
    def test_plain_integer_index(self):
        """ifTable indexed by ifIndex (Integer32). Single suffix element."""
        tree = _Tree(ifIndex=_Node(syntax="Integer32"))
        out = parse_index_suffix(tree, ["ifIndex"], (5,))
        assert out == {"ifIndex": "5"}

    def test_integer_index_with_enum(self):
        """A suffix that lands on an enum value renders the label."""
        tree = _Tree(state=_Node(syntax="INTEGER",
                                   enum_values={1: "up", 2: "down"}))
        assert parse_index_suffix(tree, ["state"], (2,)) == {"state": "down"}

    def test_integer_index_outside_enum_falls_through(self):
        """Value not in enum_values renders as the bare integer."""
        tree = _Tree(state=_Node(syntax="INTEGER",
                                   enum_values={1: "up", 2: "down"}))
        assert parse_index_suffix(tree, ["state"], (99,)) == {"state": "99"}


class TestIpAddressIndex:
    def test_ipv4_dotted(self):
        """ipAddrTable indexed by ipAdEntAddr (IpAddress). 4-byte suffix."""
        tree = _Tree(ipAdEntAddr=_Node(syntax="IpAddress"))
        out = parse_index_suffix(tree, ["ipAdEntAddr"], (192, 168, 1, 1))
        assert out == {"ipAdEntAddr": "192.168.1.1"}

    def test_ipv4_truncated_breaks(self):
        """Less than 4 bytes — partial dict, no crash."""
        tree = _Tree(ipAdEntAddr=_Node(syntax="IpAddress"))
        assert parse_index_suffix(tree, ["ipAdEntAddr"], (10, 0)) == {}


class TestOctetStringIndex:
    def test_length_prefixed_string_smi_form(self):
        """snmpCommunityIndex of OCTET STRING (pysmi's SMI-source form
        with a space). Before normalisation this matched only by the
        accidental "string" substring; pinning the case here so a
        future tightening of the constants doesn't quietly regress."""
        tree = _Tree(commIndex=_Node(syntax="OCTET STRING"))
        # length=6, "public"
        suffix = (6, 112, 117, 98, 108, 105, 99)
        assert parse_index_suffix(tree, ["commIndex"], suffix) \
            == {"commIndex": "public"}

    def test_length_prefixed_string_camel_form(self):
        tree = _Tree(commIndex=_Node(syntax="OctetString"))
        suffix = (6, 112, 117, 98, 108, 105, 99)
        assert parse_index_suffix(tree, ["commIndex"], suffix) \
            == {"commIndex": "public"}

    def test_implied_last_index_no_length_prefix(self):
        """RFC 2578 §7.7: IMPLIED last index — bytes are raw, no length."""
        tree = _Tree(commIndex=_Node(syntax="OCTET STRING"))
        # No length byte; "abc" directly
        suffix = (97, 98, 99)
        out = parse_index_suffix(tree, ["commIndex"], suffix,
                                  last_implied=True)
        assert out == {"commIndex": "abc"}

    def test_implied_only_applies_to_last(self):
        """A non-last IMPLIED would be invalid SMI; we still demand the
        length prefix on every non-last element regardless of the flag."""
        tree = _Tree(
            first=_Node(syntax="OCTET STRING"),
            second=_Node(syntax="Integer32"),
        )
        # length=2 + "ab", then integer 7
        suffix = (2, 97, 98, 7)
        out = parse_index_suffix(tree, ["first", "second"], suffix,
                                  last_implied=True)
        assert out == {"first": "ab", "second": "7"}

    def test_truncated_octet_string_breaks(self):
        """Length byte says 5 but only 2 bytes follow — break, partial out."""
        tree = _Tree(commIndex=_Node(syntax="OCTET STRING"))
        suffix = (5, 97, 98)
        assert parse_index_suffix(tree, ["commIndex"], suffix) == {}


class TestPhysAddressIndex:
    def test_mac_address(self):
        """ifPhysAddress / dot1dTpFdbAddress — length-prefixed 6 bytes."""
        tree = _Tree(mac=_Node(syntax="PhysAddress"))
        # length=6, MAC 00:AA:BB:CC:DD:EE
        suffix = (6, 0x00, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE)
        assert parse_index_suffix(tree, ["mac"], suffix) \
            == {"mac": "00:AA:BB:CC:DD:EE"}


class TestOidIndex:
    def test_length_prefixed_oid_smi_form(self):
        """A column declared as the SMI source-form ``OBJECT IDENTIFIER``
        — pysmi keeps the space in JSON output, so the parser must
        match insensitive to it. Before normalisation this fell
        through to the default INTEGER branch and silently corrupted
        the row (RMON2-MIB::usrHistoryObjectVariable hits this)."""
        tree = _Tree(ptr=_Node(syntax="OBJECT IDENTIFIER"))
        # length=3, then 1.3.6
        suffix = (3, 1, 3, 6)
        assert parse_index_suffix(tree, ["ptr"], suffix) == {"ptr": ".1.3.6"}

    def test_length_prefixed_oid_camel_form(self):
        """Some pysmi builds emit the no-space form; both must work."""
        tree = _Tree(ptr=_Node(syntax="ObjectIdentifier"))
        suffix = (3, 1, 3, 6)
        assert parse_index_suffix(tree, ["ptr"], suffix) == {"ptr": ".1.3.6"}


# --- composite indexes ----------------------------------------------------

class TestInetAddressPair:
    """RFC 4001: an InetAddressType column followed by an InetAddress
    column. The type tells the address how to render. This is the case
    every InetAddress-using table (ipNetToPhysicalEntry, tcpListener,
    udpListener, …) hits."""

    def test_ipv4(self):
        tree = _Tree(
            addrType=_Node(syntax="InetAddressType",
                            enum_values={1: "ipv4", 2: "ipv6"}),
            addr=_Node(syntax="InetAddress"),
        )
        # type=1 (ipv4), length=4, 10.0.0.1
        suffix = (1, 4, 10, 0, 0, 1)
        out = parse_index_suffix(tree, ["addrType", "addr"], suffix)
        assert out == {"addrType": "ipv4", "addr": "10.0.0.1"}

    def test_ipv6(self):
        tree = _Tree(
            addrType=_Node(syntax="InetAddressType",
                            enum_values={1: "ipv4", 2: "ipv6"}),
            addr=_Node(syntax="InetAddress"),
        )
        # type=2 (ipv6), length=16, 2001:db8::1
        v6 = (0x20, 0x01, 0x0d, 0xb8) + (0,) * 11 + (1,)
        suffix = (2, 16, *v6)
        out = parse_index_suffix(tree, ["addrType", "addr"], suffix)
        assert out["addrType"] == "ipv6"
        assert out["addr"] == "2001:0db8:0000:0000:0000:0000:0000:0001"

    def test_ipv4_without_paired_type_falls_back_on_length(self):
        """If a table has InetAddress without a preceding InetAddressType
        (rare but seen in non-conforming MIBs), we still produce a
        sensible render based on byte count."""
        tree = _Tree(addr=_Node(syntax="InetAddress"))
        suffix = (4, 10, 0, 0, 1)
        out = parse_index_suffix(tree, ["addr"], suffix)
        assert out == {"addr": "10.0.0.1"}


class TestTcpConnTable:
    """tcpConnEntry indexed by (localAddr, localPort, remoteAddr,
    remotePort). Real example pulled off a Linux box's snmpwalk:
    127.0.0.1:5432 ↔ 127.0.0.1:53124."""

    def test_full_quadruple(self):
        tree = _Tree(
            localAddr=_Node(syntax="IpAddress"),
            localPort=_Node(syntax="INTEGER"),
            remoteAddr=_Node(syntax="IpAddress"),
            remotePort=_Node(syntax="INTEGER"),
        )
        suffix = (127, 0, 0, 1, 5432, 127, 0, 0, 1, 53124)
        out = parse_index_suffix(
            tree,
            ["localAddr", "localPort", "remoteAddr", "remotePort"],
            suffix)
        assert out == {
            "localAddr": "127.0.0.1",
            "localPort": "5432",
            "remoteAddr": "127.0.0.1",
            "remotePort": "53124",
        }


class TestRfc4022TcpConnectionTable:
    """tcpConnectionTable (RFC 4022) — InetAddressType + InetAddress
    pairs on both ends. Common on modern dual-stack devices."""

    def test_v4_to_v4(self):
        tree = _Tree(
            localT=_Node(syntax="InetAddressType",
                          enum_values={1: "ipv4", 2: "ipv6"}),
            localA=_Node(syntax="InetAddress"),
            localP=_Node(syntax="InetPortNumber"),
            remoteT=_Node(syntax="InetAddressType",
                           enum_values={1: "ipv4", 2: "ipv6"}),
            remoteA=_Node(syntax="InetAddress"),
            remoteP=_Node(syntax="InetPortNumber"),
        )
        suffix = (
            1, 4, 10, 0, 0, 5,        # localT=ipv4, localA=10.0.0.5
            22,                        # localP=22
            1, 4, 10, 0, 0, 99,       # remoteT=ipv4, remoteA=10.0.0.99
            46123,                     # remoteP
        )
        out = parse_index_suffix(
            tree,
            ["localT", "localA", "localP",
             "remoteT", "remoteA", "remoteP"],
            suffix)
        assert out == {
            "localT": "ipv4",
            "localA": "10.0.0.5",
            "localP": "22",
            "remoteT": "ipv4",
            "remoteA": "10.0.0.99",
            "remoteP": "46123",
        }


# --- defensive cases ------------------------------------------------------

class TestDefensive:
    def test_unknown_index_breaks(self):
        """Resolver returns None for an index name we don't have a node
        for — partial dict, no crash."""
        tree = _Tree(ifIndex=_Node(syntax="Integer32"))
        out = parse_index_suffix(tree, ["ifIndex", "ghost"], (1, 2))
        assert out == {"ifIndex": "1"}

    def test_empty_suffix_returns_empty(self):
        tree = _Tree(ifIndex=_Node(syntax="Integer32"))
        assert parse_index_suffix(tree, ["ifIndex"], ()) == {}

    def test_empty_index_list_returns_empty(self):
        tree = _Tree()
        assert parse_index_suffix(tree, [], (1, 2, 3)) == {}

    def test_extra_suffix_bytes_ignored(self):
        """If declared indices consume less than the suffix has, the rest
        is just dropped — a partial parse is better than no parse."""
        tree = _Tree(ifIndex=_Node(syntax="Integer32"))
        out = parse_index_suffix(tree, ["ifIndex"], (5, 99, 99))
        assert out == {"ifIndex": "5"}


# --- render_octet_index ---------------------------------------------------

class TestRenderOctetIndex:
    def test_inet_address_v4_via_addr_type(self):
        assert render_octet_index("inetaddress", [10, 0, 0, 1], 1) \
            == "10.0.0.1"

    def test_inet_address_v6_via_addr_type(self):
        raw = [0x20, 0x01, 0x0d, 0xb8] + [0] * 11 + [1]
        assert render_octet_index("inetaddress", raw, 2) \
            == "2001:0db8:0000:0000:0000:0000:0000:0001"

    def test_inet_address_falls_back_on_length_when_type_missing(self):
        assert render_octet_index("inetaddress", [192, 168, 1, 1], None) \
            == "192.168.1.1"

    def test_physaddress_uppercase_colons(self):
        assert render_octet_index("physaddress", [0x12, 0xab, 0x34], None) \
            == "12:AB:34"

    def test_octet_string_printable_ascii(self):
        assert render_octet_index("octetstring",
                                    [104, 101, 108, 108, 111], None) \
            == "hello"

    def test_octet_string_non_printable_falls_back_to_hex(self):
        assert render_octet_index("octetstring", [0x00, 0xff, 0x10], None) \
            == "00 FF 10"
