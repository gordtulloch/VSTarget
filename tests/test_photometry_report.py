"""Tests for the AAVSO WebObs report formatting in photometry.py."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from photometry import PhotometryResult, format_aavso_report, format_summary


@pytest.fixture
def result() -> PhotometryResult:
    return PhotometryResult(
        star_name="RW Aur",
        fits_path=r"C:\images\rwaur.fit",
        filter_band="V",
        obs_date=datetime(2026, 7, 1, 4, 30, 0, tzinfo=timezone.utc),
        jd=2461222.6875,
        target_mag=10.523,
        mag_error=0.031,
        chart_id="X12345ABC",
        comp_star="000-BBC-123",
        comp_mag=11.2,
        check_star="000-BBC-456",
        check_mag_measured=12.301,
        check_mag_catalog=12.312,
        airmass=1.234,
        ensemble_stars=["000-BBC-123", "000-BBC-456"],
        notes="Ensemble of 2 stars; RMS=0.0310",
        observer_code="TGOR",
    )


def test_report_header_block(result):
    lines = format_aavso_report(result).splitlines()
    assert lines[0] == "#TYPE=Extended"
    assert "#OBSCODE=TGOR" in lines
    assert "#SOFTWARE=VSTarget" in lines
    assert "#DELIM=," in lines
    assert "#DATE=JD" in lines
    assert "#OBSTYPE=CCD" in lines


def test_report_data_line_fields(result):
    data_line = format_aavso_report(result).splitlines()[-1]
    fields = data_line.split(",")
    # NAME,DATE,MAG,MERR,FILT,TRANS,MTYPE,CNAME,CMF,KNAME,KMF,AMASS,GROUP,CHART,NOTES
    assert fields[0] == "RWAur"  # spaces stripped from star name
    assert fields[1] == "2461222.68750"
    assert fields[2] == "10.523"
    assert fields[3] == "0.031"
    assert fields[4] == "V"
    assert fields[5] == "NO"
    assert fields[6] == "STD"
    assert fields[7] == "ENSEMBLE"
    assert fields[8] == "na"
    assert fields[9] == "000-BBC-456"
    assert fields[10] == "12.312"
    assert fields[11] == "1.234"
    assert fields[12] == "0"
    assert fields[13] == "X12345ABC"


def test_report_airmass_na_when_zero(result):
    result.airmass = 0.0
    data_line = format_aavso_report(result).splitlines()[-1]
    assert data_line.split(",")[11] == "na"


def test_report_ends_with_newline(result):
    assert format_aavso_report(result).endswith("\n")


def test_summary_contains_key_values(result):
    summary = format_summary(result)
    assert "RW Aur" in summary
    assert "10.523" in summary
    assert "0.031" in summary
    assert "X12345ABC" in summary
    assert "rwaur.fit" in summary
