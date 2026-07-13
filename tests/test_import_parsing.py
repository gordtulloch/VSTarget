"""Tests for the target-file import helpers in main_window.py.

These are module-level pure functions; importing main_window does not
require a QApplication.
"""
from __future__ import annotations

import pytest

from main_window import _parse_coord, _parse_mag, _parse_target_text, _split_fields

# ── _split_fields ─────────────────────────────────────────────────────────────


def test_split_fields_tabs():
    fields = _split_fields("Z And\t23 33 39.95 +48 49 05.9\tZAND\t7.7 - 11.3 V")
    assert fields == ["Z And", "23 33 39.95 +48 49 05.9", "ZAND", "7.7 - 11.3 V"]


def test_split_fields_multiple_spaces():
    fields = _split_fields("SS Cyg   21 42 42.79 +43 35 09.9   UGSS   7.7 - 12.4 V")
    assert fields == ["SS Cyg", "21 42 42.79 +43 35 09.9", "UGSS", "7.7 - 12.4 V"]


def test_split_fields_single_spaces_uses_coord_regex():
    fields = _split_fields("Z And 23 33 39.95 +48 49 05.9 ZAND 7.7 - 11.3 V")
    assert fields == ["Z And", "23 33 39.95 +48 49 05.9", "ZAND", "7.7 - 11.3 V"]


def test_split_fields_unparseable_returns_whole_line():
    assert _split_fields("garbage") == ["garbage"]


# ── _parse_coord ──────────────────────────────────────────────────────────────


def test_parse_coord_positive_dec():
    ra, dec = _parse_coord("23 33 39.95 +48 49 05.9")
    assert ra == pytest.approx((23 + 33 / 60 + 39.95 / 3600) * 15.0)
    assert dec == pytest.approx(48 + 49 / 60 + 5.9 / 3600)


def test_parse_coord_negative_dec():
    ra, dec = _parse_coord("21 12 09.20 -08 49 37.0")
    assert dec == pytest.approx(-(8 + 49 / 60 + 37.0 / 3600))


def test_parse_coord_space_after_sign_collapsed():
    _, dec = _parse_coord("01 02 03.0 - 10 20 30.0")
    assert dec == pytest.approx(-(10 + 20 / 60 + 30.0 / 3600))


def test_parse_coord_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        _parse_coord("")


def test_parse_coord_too_few_tokens_raises():
    with pytest.raises(ValueError, match="6 tokens"):
        _parse_coord("23 33 39.95")


def test_parse_coord_non_numeric_ra_raises():
    with pytest.raises(ValueError, match="non-numeric RA"):
        _parse_coord("xx 33 39.95 +48 49 05.9")


def test_parse_coord_ra_in_degrees_rejected():
    # 353 > 24 h: user pasted degrees instead of hours
    with pytest.raises(ValueError, match="out of range"):
        _parse_coord("353 25 00.0 +48 49 05.9")


# ── _parse_mag ────────────────────────────────────────────────────────────────


def test_parse_mag_range_with_band():
    assert _parse_mag("7.7 - 11.3 V") == (7.7, 11.3, "V")


def test_parse_mag_single_value():
    max_mag, min_mag, band = _parse_mag("12.5 V")
    assert max_mag == 12.5
    assert min_mag == 12.5
    assert band == "V"


def test_parse_mag_empty():
    assert _parse_mag("") == (None, None, "")


# ── _parse_target_text ────────────────────────────────────────────────────────

_DEFAULTS = ("V,B,I", "4,4,4", "30,30,30", "1,1,1")

_SAMPLE = """Name\tCoords\tType\tMag
Z And\t23 33 39.95 +48 49 05.9\tZAND\t7.7 - 11.3 V
SS Cyg\t21 42 42.79 +43 35 09.9\tUGSS\t7.7 - 12.4 V
"""


def test_parse_target_text_skips_header_and_parses_rows():
    targets, errors = _parse_target_text(_SAMPLE, _DEFAULTS)
    assert errors == []
    assert [t.aavso.star_name for t in targets] == ["Z And", "SS Cyg"]


def test_parse_target_text_extracts_constellation_from_name():
    targets, _ = _parse_target_text(_SAMPLE, _DEFAULTS)
    assert targets[0].aavso.constellation == "And"
    assert targets[1].aavso.constellation == "Cyg"


def test_parse_target_text_applies_script_defaults():
    targets, _ = _parse_target_text(_SAMPLE, _DEFAULTS)
    t = targets[0]
    assert t.script_filters == "V,B,I"
    assert t.script_counts == "4,4,4"
    assert t.script_intervals == "30,30,30"
    assert t.script_binning == "1,1,1"


def test_parse_target_text_reports_bad_lines_with_line_numbers():
    text = "Z And\t23 33 39.95 +48 49 05.9\tZAND\t7.7\nBroken\tnot coords\tX\t1.0\n"
    targets, errors = _parse_target_text(text, _DEFAULTS)
    assert len(targets) == 1
    assert len(errors) == 1
    assert "Line 2" in errors[0]
    assert "Broken" in errors[0]


def test_parse_target_text_ignores_blank_lines():
    targets, errors = _parse_target_text("\n\n" + _SAMPLE + "\n\n", _DEFAULTS)
    assert len(targets) == 2
    assert errors == []
