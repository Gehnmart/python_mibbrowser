"""_evaluate_condition is a pure function but lives under ui/ — import
it directly to avoid Qt setup for this test."""
from pymibbrowser.ui.watches_tab import _evaluate_condition


def test_numeric_gt():
    assert _evaluate_condition("100", ">", "50") is True
    assert _evaluate_condition("10", ">", "50") is False


def test_numeric_ge_le():
    assert _evaluate_condition("50", ">=", "50") is True
    assert _evaluate_condition("49", ">=", "50") is False
    assert _evaluate_condition("50", "<=", "50") is True


def test_numeric_eq_ne():
    assert _evaluate_condition("1", "==", "1") is True
    assert _evaluate_condition("1", "!=", "1") is False


def test_string_fallback():
    # Not a float → string compare works for ==/!=.
    assert _evaluate_condition("up", "==", "up") is True
    assert _evaluate_condition("up", "!=", "down") is True


def test_string_gt_returns_none():
    # Can't 'greater than' a string.
    assert _evaluate_condition("foo", ">", "5") is None


def test_empty_threshold_is_none():
    # Empty RHS shouldn't crash — returns None for ordered ops.
    assert _evaluate_condition("5", ">", "") is None


def test_unknown_op_returns_none():
    assert _evaluate_condition("5", "~=", "3") is None


def test_value_with_unit_suffix():
    # "111 packets" should still threshold sensibly — pull the leading
    # numeric token. The original report was "value=110 / threshold=110
    # alarms" — sanity-check both halves of the strict-> at the boundary.
    assert _evaluate_condition("111 packets", ">", "110") is True
    assert _evaluate_condition("110 packets", ">", "110") is False
    assert _evaluate_condition("110 packets", ">=", "110") is True


def test_timeticks_uses_raw_count_in_parens():
    # _format_timeticks ends each render with the raw 1/100s count in
    # parens — that's the unit a user types in the threshold field.
    assert _evaluate_condition("1 minute 51.00 seconds (11100)",
                                ">", "11000") is True
    assert _evaluate_condition("1 minute 51.00 seconds (11100)",
                                ">", "11200") is False


def test_strips_whitespace_for_string_compare():
    assert _evaluate_condition(" up ", "==", "up") is True
