"""Tests for stack.py — solve-provenance stripping in the output header."""
from __future__ import annotations

from astropy.io import fits

from stack import strip_solve_provenance


def _pinpoint_header() -> fits.Header:
    """Header mimicking an iTelescope calibrated frame solved by PinPoint."""
    h = fits.Header()
    h["CTYPE1"] = "RA---TAN"
    h["CTYPE2"] = "DEC--TAN"
    h["CRVAL1"] = 240.42
    h["CRVAL2"] = 66.80
    h["PLTSOLVD"] = (True, "Plate has been solved by PinPoint")
    h["OBJECT"] = "AG Dra"
    h["EXPTIME"] = 10.0
    h["HISTORY"] = "File was processed by PinPoint 7.0.0 at 2026-07-15T05:30:49"
    h["HISTORY"] = "WCS added by PinPoint 7.0.0 at 2026-07-15T05:30:54"
    h["HISTORY"] = "Dark subtracted with master dark"
    h["COMMENT"] = 'Solved in 0.1 sec. Offset 0.5"'
    h["COMMENT"] = "Calibrated by iTelescope.net"
    return h


def test_strips_pltsolvd_and_solver_lines():
    h = _pinpoint_header()
    strip_solve_provenance(h)

    assert "PLTSOLVD" not in h
    history = [str(c) for c in h["HISTORY"]]
    assert history == ["Dark subtracted with master dark"]
    comments = [str(c) for c in h["COMMENT"]]
    assert comments == ["Calibrated by iTelescope.net"]


def test_keeps_wcs_and_other_cards():
    h = _pinpoint_header()
    strip_solve_provenance(h)

    assert h["CTYPE1"] == "RA---TAN"
    assert h["CRVAL1"] == 240.42
    assert h["OBJECT"] == "AG Dra"
    assert h["EXPTIME"] == 10.0


def test_strips_astap_provenance():
    h = fits.Header()
    h["PLTSOLVD"] = (True, "Astrometric solved by ASTAP v2026.06.29.")
    h["COMMENT"] = '7  Solved in 0.1 sec. Offset 0.5"'
    h["COMMENT"] = 'cmdline:"C:\\Program Files\\astap\\astap.exe" -f "x.fit"'
    strip_solve_provenance(h)

    assert "PLTSOLVD" not in h
    assert "COMMENT" not in h


def test_noop_on_unsolved_header():
    h = fits.Header()
    h["OBJECT"] = "AG Dra"
    h["HISTORY"] = "Bias subtracted"
    strip_solve_provenance(h)

    assert h["OBJECT"] == "AG Dra"
    assert [str(c) for c in h["HISTORY"]] == ["Bias subtracted"]
