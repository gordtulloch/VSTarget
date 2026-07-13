"""Tests for exposure.py — calibration math and exposure suggestions."""
from __future__ import annotations

import math

import pytest

from exposure import (
    EXPOSURE_LADDER,
    ExposureCalibration,
    fit_zeropoint,
    normalize_telescope,
    suggest_exposure,
)


@pytest.fixture
def calib() -> ExposureCalibration:
    # Values close to those measured from the real T05 BZ UMa field
    return ExposureCalibration(
        telescope="T5",
        filter_band="V",
        zeropoint=15.8,
        sky_rate=4.9,
        peak_frac=0.15,
        aperture_radius=6.0,
        n_stars=8,
        zp_rms=0.05,
    )


# ── fit_zeropoint ─────────────────────────────────────────────────────────────


def test_fit_zeropoint_recovers_exact_zp():
    zp_true, t = 20.0, 30.0
    mags = [10.0, 12.0, 14.0]
    fluxes = [10 ** (0.4 * (zp_true - m)) * t for m in mags]
    zp, rms, n = fit_zeropoint(mags, fluxes, t)
    assert zp == pytest.approx(zp_true)
    assert rms == pytest.approx(0.0, abs=1e-9)
    assert n == 3


def test_fit_zeropoint_excludes_nonpositive_flux():
    zp, _rms, n = fit_zeropoint([10.0, 12.0], [1000.0, -5.0], 30.0)
    assert n == 1
    assert zp == pytest.approx(10.0 + 2.5 * math.log10(1000.0 / 30.0))


def test_fit_zeropoint_rejects_bad_exptime():
    with pytest.raises(ValueError, match="positive"):
        fit_zeropoint([10.0], [100.0], 0.0)


def test_fit_zeropoint_rejects_all_negative_fluxes():
    with pytest.raises(ValueError, match="no comparison"):
        fit_zeropoint([10.0], [-1.0], 30.0)


def test_fit_zeropoint_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        fit_zeropoint([10.0, 11.0], [100.0], 30.0)


# ── Calibration model ─────────────────────────────────────────────────────────


def test_flux_rate_five_magnitudes_is_100x(calib):
    assert calib.flux_rate(10.0) / calib.flux_rate(15.0) == pytest.approx(100.0)


def test_snr_increases_with_exposure(calib):
    assert calib.snr(12.0, 60.0) > calib.snr(12.0, 30.0)


def test_exposure_for_snr_is_inverse_of_snr(calib):
    t = calib.exposure_for_snr(12.0, 50.0)
    assert calib.snr(12.0, t) == pytest.approx(50.0)


def test_saturation_exposure_shorter_for_brighter_stars(calib):
    assert calib.saturation_exposure(8.0) < calib.saturation_exposure(12.0)


def test_dict_round_trip(calib):
    assert ExposureCalibration.from_dict(calib.to_dict()) == calib


def test_from_dict_ignores_unknown_keys(calib):
    d = calib.to_dict()
    d["future_field"] = 1
    assert ExposureCalibration.from_dict(d) == calib


# ── suggest_exposure ──────────────────────────────────────────────────────────


def test_suggestion_on_ladder_and_reaches_snr(calib):
    s = suggest_exposure(calib, faint_mag=10.0)
    assert s.seconds in EXPOSURE_LADDER
    assert s.expected_snr >= 50.0
    assert not s.capped_by_comp
    assert s.warnings == []


def test_fainter_target_gets_longer_exposure(calib):
    t10 = suggest_exposure(calib, faint_mag=10.0).seconds
    t12 = suggest_exposure(calib, faint_mag=12.0).seconds
    assert t12 > t10


def test_faint_target_clamped_to_max_exposure(calib):
    s = suggest_exposure(calib, faint_mag=16.0, max_exposure=300.0)
    assert s.seconds == 300
    assert s.capped_by_comp
    assert any("clamped" in w for w in s.warnings)


def test_comp_star_saturation_is_hard_cap(calib):
    # Bright comps (mag 6) saturate quickly; the suggestion must stay below
    # that limit even though the faint target wants far more time.
    s = suggest_exposure(
        calib, faint_mag=15.0, comp_bright_mag=6.0, max_exposure=600.0
    )
    assert s.seconds <= calib.saturation_exposure(6.0)
    assert s.capped_by_comp
    assert any("comparison" in w for w in s.warnings)


def test_bright_end_saturation_warns_but_does_not_shorten(calib):
    base = suggest_exposure(calib, faint_mag=10.0)
    s = suggest_exposure(calib, faint_mag=10.0, bright_mag=5.0)
    assert s.seconds == base.seconds  # warning only, exposure unchanged
    assert s.target_may_saturate
    assert any("bright end" in w for w in s.warnings)


def test_no_bright_end_warning_when_safe(calib):
    s = suggest_exposure(calib, faint_mag=12.0, bright_mag=11.5)
    assert not s.target_may_saturate


# ── normalize_telescope ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("T05", "T5"),
        ("T5 – New Mexico Skies (Mayhill, NM)", "T5"),
        ("T24 – New Mexico Skies (Mayhill, NM)", "T24"),
        ("iTelescope T30", "T30"),
        ("iTelescope 5", "T5"),  # actual TELESCOP header value on iTelescope
        ("iTelescope 24", "T24"),
        ("calibrated-T05-user-BZ UMa-file.fit", "T5"),
        ("", "UNKNOWN"),
        ("Backyard Scope", "Backyard Scope"),
    ],
)
def test_normalize_telescope(raw, expected):
    assert normalize_telescope(raw) == expected
