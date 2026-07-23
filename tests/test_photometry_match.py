"""Tests for photometry.py's match_and_measure() — shared by run_photometry()
and the standard-field transform generator."""
from __future__ import annotations

import math

import numpy as np
from astropy.wcs import WCS

from photometry import match_and_measure


def _simple_wcs() -> WCS:
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [10.0, 20.0]
    w.wcs.crpix = [50.0, 50.0]
    w.wcs.cdelt = [-0.001, 0.001]
    return w


def _fake_sources(entries: list[tuple[float, float, float]]) -> np.ndarray:
    dtype = np.dtype([("x", "f4"), ("y", "f4"), ("peak", "f4")])
    arr = np.zeros(len(entries), dtype=dtype)
    for i, (x, y, peak) in enumerate(entries):
        arr[i] = (x, y, peak)
    return arr


def test_matches_star_at_reference_pixel_and_measures_flux():
    wcs = _simple_wcs()
    data_sub = np.full((100, 100), 5.0)  # flat bright field around the reference pixel

    stars = [{"auid": "ref-star", "ra": "00:40:00", "dec": "+20:00:00"}]  # RA=10deg, Dec=20deg
    sources = _fake_sources([(50.0, 50.0, 999.0)])

    df = match_and_measure(data_sub, sources, stars, wcs, aperture_radius=4.0)

    assert list(df["auid"]) == ["ref-star"]
    assert df.iloc[0]["aperture_sum"] > 0
    expected_mag = -2.5 * math.log10(df.iloc[0]["aperture_sum"])
    assert df.iloc[0]["instrumental_mag"] == expected_mag


def test_unmatched_star_is_dropped():
    wcs = _simple_wcs()
    data_sub = np.full((100, 100), 5.0)

    stars = [
        {"auid": "near", "ra": "00:40:00", "dec": "+20:00:00"},   # maps to (50, 50)
        {"auid": "far",  "ra": "01:00:00", "dec": "+40:00:00"},   # far outside the field
    ]
    sources = _fake_sources([(50.0, 50.0, 999.0)])

    df = match_and_measure(data_sub, sources, stars, wcs, aperture_radius=4.0)

    assert list(df["auid"]) == ["near"]


def test_no_matches_returns_empty_dataframe():
    wcs = _simple_wcs()
    data_sub = np.zeros((100, 100))
    stars = [{"auid": "lonely", "ra": "00:40:00", "dec": "+20:00:00"}]
    sources = _fake_sources([])  # nothing detected

    df = match_and_measure(data_sub, sources, stars, wcs, aperture_radius=4.0)

    assert df.empty
