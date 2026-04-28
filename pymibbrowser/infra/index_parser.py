"""Decompose SNMP-table-row OID suffixes into rendered index columns.

Lives outside ``ui/`` because the logic is pure (tree lookups + arithmetic
on integers) and the rules — INET address typing, IMPLIED last index,
length-prefixed octets — are tricky enough that having a Qt-free test
surface matters more than co-location with TableViewTab. Vendor MIBs
push every edge of this function; treat the test file as the spec.

References:
  - RFC 2578 §7.7         (IMPLIED on the last index)
  - RFC 4001              (InetAddressType + InetAddress encoding)
  - SMIv2 (RFC 2578-2580) (general index encoding rules)
"""
from __future__ import annotations

from typing import Protocol

# SMI types used as table indices. These are textual conventions
# ultimately resolving to a small set of base encodings.
OCTET_LIKE_INDEX = (
    "octetstring", "display", "string",
    "inetaddress",           # RFC 4001: length-prefixed octet string
    "physaddress", "mac",    # physical addr: length-prefixed
    "bits",
)
IPV4_INDEX = ("ipaddress",)
OID_LIKE_INDEX = ("objectidentifier", "objectname")


class _NodeView(Protocol):
    """The slice of MibNode this module reads. Tests can pass any
    object that exposes these names — no MibNode dependency."""
    syntax: str
    enum_values: dict[int, str]


class _TreeView(Protocol):
    def node_by_name(self, name: str) -> _NodeView | None: ...


def parse_index_suffix(tree: _TreeView,
                        index_names: list[str],
                        suffix: tuple[int, ...],
                        last_implied: bool = False) -> dict[str, str]:
    """Decompose an OID suffix into ``{index-column-name: rendered-value}``.

    Recognised encodings:
      - ``IpAddress``           — exactly 4 elements, dotted quad
      - ``InetAddressType``     — 1 INTEGER (enum), remembered for the
                                  next ``InetAddress`` to disambiguate
                                  IPv4 vs IPv6
      - ``InetAddress`` /
        ``OCTET STRING`` /
        ``PhysAddress``         — length-prefixed (octet count, then bytes)
      - ``OID`` / ``ObjectName`` — length-prefixed (sub-id count, then ids)
      - ``IMPLIED`` last index  — RFC 2578 §7.7: no length prefix; the
                                  rest of the suffix IS the value
      - INTEGER family          — 1 element

    Truncation (suffix shorter than the declared index list) returns
    a partial dict, never raises — callers display whatever they got.
    """
    out: dict[str, str] = {}
    remaining = list(suffix)
    last_addr_type: int | None = None    # IPv4 vs IPv6 carried from preceding type

    total = len(index_names)
    for pos, name in enumerate(index_names):
        is_last = pos == total - 1
        node = tree.node_by_name(name)
        if node is None or not remaining:
            break
        # pysmi writes the SMI source form verbatim into syntax.type —
        # so OCTET STRING / OBJECT IDENTIFIER come through with spaces.
        # Strip them once here, instead of bloating each constant tuple
        # with both spelling variants.
        syntax = (node.syntax or "").lower().replace(" ", "")

        # Enum-carrying INTEGERs (e.g. InetAddressType whose syntax is
        # the textual convention name, not "INTEGER") MUST be matched
        # before the octet-like branch — otherwise "InetAddressType"
        # collides with the "inetaddress" substring check below.
        if node.enum_values or syntax.endswith("type"):
            value = remaining.pop(0)
            out[name] = (node.enum_values.get(value, str(value))
                          if node.enum_values else str(value))
            if "addresstype" in syntax or name.lower().endswith("addresstype"):
                last_addr_type = value
            continue

        if any(k in syntax for k in IPV4_INDEX):
            if len(remaining) < 4:
                break
            out[name] = ".".join(str(p) for p in remaining[:4])
            remaining = remaining[4:]
            continue

        if any(k in syntax for k in OCTET_LIKE_INDEX):
            # Length-prefixed unless this is the LAST index and the row
            # declaration says IMPLIED — then the suffix IS the value
            # with no leading length byte.
            if is_last and last_implied:
                raw_bytes = remaining
                remaining = []
            else:
                length = remaining.pop(0)
                if len(remaining) < length:
                    break
                raw_bytes = remaining[:length]
                remaining = remaining[length:]
            out[name] = render_octet_index(syntax, raw_bytes, last_addr_type)
            continue

        if any(k in syntax for k in OID_LIKE_INDEX):
            if is_last and last_implied:
                parts = remaining
                remaining = []
            else:
                length = remaining.pop(0)
                if len(remaining) < length:
                    break
                parts = remaining[:length]
                remaining = remaining[length:]
            out[name] = "." + ".".join(str(p) for p in parts)
            continue

        # Default: one-element integer (INTEGER, Integer32, Unsigned32,
        # Counter*, Gauge*, TimeTicks, InetPortNumber, …).
        value = remaining.pop(0)
        if node.enum_values and value in node.enum_values:
            out[name] = node.enum_values[value]
        else:
            out[name] = str(value)
        if "addresstype" in syntax or name.lower().endswith("addresstype"):
            last_addr_type = value
    return out


def render_octet_index(syntax: str, raw: list[int],
                        addr_type: int | None) -> str:
    """Render a length-prefixed octet sequence as a human string.

    InetAddress disambiguation: ``addr_type`` carries the value of a
    preceding ``InetAddressType`` index — 1=ipv4, 2=ipv6, 3=ipv4z,
    4=ipv6z, 16=dns. When absent (table doesn't pair the type with
    the address) we fall back to the byte length (4 → IPv4, 16 →
    IPv6).

    Callers from ``parse_index_suffix`` already pass a normalised
    syntax (lowercased, spaces stripped); when called directly we
    normalise here too.
    """
    syntax = syntax.lower().replace(" ", "")
    if "inetaddress" in syntax:
        if addr_type in (1, 3) and len(raw) >= 4:
            return ".".join(str(b) for b in raw[:4])
        if addr_type in (2, 4) and len(raw) >= 16:
            hexes = [f"{raw[i]:02x}{raw[i + 1]:02x}"
                     for i in range(0, 16, 2)]
            return ":".join(hexes)
        # Fallback by length alone.
        if len(raw) == 4:
            return ".".join(str(b) for b in raw)
        if len(raw) == 16:
            hexes = [f"{raw[i]:02x}{raw[i + 1]:02x}"
                     for i in range(0, 16, 2)]
            return ":".join(hexes)
    if "physaddress" in syntax or "mac" in syntax:
        return ":".join(f"{b:02X}" for b in raw)
    # DisplayString / OCTET STRING — printable ASCII goes through, the
    # rest is rendered as space-separated hex.
    b = bytes(raw)
    if all(32 <= c < 127 for c in b):
        return b.decode("latin-1")
    return " ".join(f"{c:02X}" for c in raw)
