"""Round-trip walk-file format: Save walk writer ↔ Compare parser.

Covers the escaped-quotes regression: a sysDescr with embedded quotes
used to hit the first inner quote and stop parsing early."""
from __future__ import annotations

from dataclasses import dataclass

from pymibbrowser.ui.compare_dialog import (
    _unescape_quoted,
    parse_walk_file,
)
from pymibbrowser.ui.save_walk_dialog import _escape_quoted, _format_line


@dataclass
class _VB:
    oid: tuple
    type_name: str = "OctetString"
    value: object = None
    display_value: str = ""


def test_escape_unescape_identity():
    for s in ("plain", 'has "quotes"', r'path\to\file', ""):
        assert _unescape_quoted(f'"{_escape_quoted(s)}"') == s


def test_format_line_string():
    vb = _VB(oid=(1, 3, 6, 1, 2, 1, 1, 1, 0),
             type_name="OctetString", display_value="Linux lab")
    line = _format_line(vb)
    assert line == '.1.3.6.1.2.1.1.1.0 = STRING: "Linux lab"'


def test_format_line_timeticks_unquoted():
    vb = _VB(oid=(1, 3, 6, 1, 2, 1, 1, 3, 0),
             type_name="TimeTicks", display_value="2 days 3 hours")
    line = _format_line(vb)
    # Timeticks isn't quoted — matches snmpwalk output.
    assert '"' not in line
    assert "Timeticks: 2 days 3 hours" in line


def test_parse_unquoted():
    with_pre = ".1.3.6.1.2.1.1.3.0 = Timeticks: (12345) 0:02:03.45\n"
    d = _parse_str(with_pre)
    assert d[(1, 3, 6, 1, 2, 1, 1, 3, 0)] == "(12345) 0:02:03.45"


def test_parse_quoted_plain():
    d = _parse_str('.1.3.6.1.2.1.1.1.0 = STRING: "Linux lab"\n')
    assert d[(1, 3, 6, 1, 2, 1, 1, 1, 0)] == "Linux lab"


def test_parse_quoted_with_escaped_quotes():
    d = _parse_str(
        '.1.3.6.1.2.1.1.1.0 = STRING: "Linux a\\"b\\" c"\n')
    assert d[(1, 3, 6, 1, 2, 1, 1, 1, 0)] == 'Linux a"b" c'


def test_parse_skips_blank_and_garbage():
    d = _parse_str(
        "\n"
        "# comment\n"
        "not a walk line\n"
        '.1.3.6.1.2.1.1.5.0 = STRING: "host"\n')
    assert d == {(1, 3, 6, 1, 2, 1, 1, 5, 0): "host"}


def test_roundtrip_preserves_quotes_and_backslash(tmp_path):
    # Newlines aren't supported (walk-file format is line-oriented), but
    # embedded quotes + backslashes are the cases that broke parsing.
    original = r'contact "admin@example.com" ok \path\here'
    vb = _VB(oid=(1, 3, 6, 1, 2, 1, 1, 4, 0),
             type_name="OctetString", display_value=original)
    line = _format_line(vb)
    p = tmp_path / "sample.walk"
    p.write_text(line + "\n")
    parsed = parse_walk_file(str(p))
    assert parsed[(1, 3, 6, 1, 2, 1, 1, 4, 0)] == original


# ---------------------------------------------------------------------------

def _parse_str(contents: str) -> dict:
    """Small helper — write to a temp file so we exercise the real
    parse_walk_file path, but callers pass string literals inline."""
    import os
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".walk",
                                      delete=False) as t:
        t.write(contents)
        name = t.name
    try:
        return parse_walk_file(name)
    finally:
        os.unlink(name)
