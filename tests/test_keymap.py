import pytest

from retrozashi.desktop.keymap import parse_combo, parse_hotkey


def test_combos():
    assert parse_combo("ctrl+s") == [0x11, 0x53]
    assert parse_combo("alt+Tab") == [0x12, 0x09]
    assert parse_combo("Return") == [0x0D]
    assert parse_combo("Page_Down") == [0x22]
    assert parse_combo("F11") == [0x7A]


def test_unknown_key():
    with pytest.raises(ValueError):
        parse_combo("ctrl+banana")


def test_hotkey():
    assert parse_hotkey("ctrl+alt+k") == (0x2 | 0x1, 0x4B)
