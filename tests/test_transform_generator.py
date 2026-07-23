"""Tests for the transform math and VSP standard-field parsing in transform_generator.py."""
from __future__ import annotations

import pytest

from transform_generator import (
    STANDARD_FIELD_NAMES,
    STANDARD_FIELDS,
    TRANSFORM_DEFS,
    LANDOLT_FIELD,
    TransformPoint,
    TransformResult,
    available_transforms,
    compute_transforms,
    fetch_standard_field_stars,
    run_transform_generation,
)


# ── Standard field registry ─────────────────────────────────────────────────

def test_standard_field_names_include_landolt():
    assert LANDOLT_FIELD in STANDARD_FIELD_NAMES
    assert set(STANDARD_FIELDS) <= set(STANDARD_FIELD_NAMES)
    assert LANDOLT_FIELD not in STANDARD_FIELDS


# ── Filter-availability gating ──────────────────────────────────────────────

def test_available_transforms_bv_only():
    names = available_transforms({"b", "v"})
    assert set(names) == {"Tbv", "Tb_bv", "Tv_bv"}


def test_available_transforms_adds_u_transforms():
    names = available_transforms({"u", "b", "v"})
    assert {"Tub", "Tu_ub", "Tb_ub"} <= set(names)


def test_available_transforms_r_vi_needs_all_three():
    # Tr_vi needs v, i AND r
    assert "Tr_vi" not in available_transforms({"v", "i"})
    assert "Tr_vi" in available_transforms({"v", "i", "r"})


def test_available_transforms_empty_for_single_filter():
    assert available_transforms({"v"}) == []


# ── compute_transforms / regression math ────────────────────────────────────

def _synthetic_stars(slope: float, n: int = 8) -> list[dict]:
    """Stars where (b-v) = (B-V)/slope exactly, so Tbv (=1/fit_slope) == slope."""
    stars = []
    for i in range(n):
        bv_std = 0.3 + i * 0.15
        bv_inst = bv_std / slope
        B = 12.0 + i * 0.1
        V = B - bv_std
        b = 12.5 + i * 0.1
        v = b - bv_inst
        stars.append({"auid": f"s{i}", "B": B, "V": V, "b": b, "v": v})
    return stars


def test_compute_transforms_reciprocal_value():
    stars = _synthetic_stars(slope=2.0)
    results = compute_transforms(stars, {"b", "v"})
    assert set(results) == {"Tbv", "Tb_bv", "Tv_bv"}
    assert results["Tbv"].value == pytest.approx(2.0)
    assert results["Tbv"].r_squared == pytest.approx(1.0)


def test_compute_transforms_skips_stars_missing_data():
    stars = _synthetic_stars(slope=1.5, n=5)
    stars.append({"auid": "incomplete", "B": 12.0, "V": 11.0})  # no b/v measured
    results = compute_transforms(stars, {"b", "v"})
    assert len(results["Tbv"].points) == 5  # incomplete star excluded


def test_transform_defs_names_are_unique():
    names = [name for name, *_ in TRANSFORM_DEFS]
    assert len(names) == len(set(names))


# ── TransformResult / interactive outlier rejection ─────────────────────────

def test_recompute_excludes_unused_points():
    # 3 points fit a slope of 1.0 exactly; a 4th is a wild outlier.
    points = [
        TransformPoint(auid="a", x=0.0, y=0.0),
        TransformPoint(auid="b", x=1.0, y=1.0),
        TransformPoint(auid="c", x=2.0, y=2.0),
        TransformPoint(auid="outlier", x=3.0, y=100.0),
    ]
    result = TransformResult(
        name="Tv_bv", x_label="B-V", y_label="V-v",
        value=0.0, error=0.0, r_squared=0.0, points=points,
    )
    result.recompute()
    assert result.r_squared < 0.9  # outlier drags the fit down

    points[-1].used = False
    result.recompute()
    assert result.value == pytest.approx(1.0)
    assert result.r_squared == pytest.approx(1.0)


def test_recompute_with_fewer_than_two_points_is_zero():
    points = [TransformPoint(auid="a", x=0.0, y=0.0)]
    result = TransformResult(
        name="Tbv", x_label="B-V", y_label="b-v",
        value=1.0, error=1.0, r_squared=1.0, points=points,
    )
    result.recompute()
    assert result.value == 0.0
    assert result.error == 0.0
    assert result.r_squared == 0.0


# ── VSP standard-field JSON parsing ──────────────────────────────────────────

class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


def test_fetch_standard_field_stars_parses_bands(monkeypatch):
    payload = {
        "photometry": [
            {
                "auid": "000-BLG-886",
                "ra": "08:51:18.9",
                "dec": "+11:48:16",
                "bands": [
                    {"band": "B", "mag": 13.5},
                    {"band": "V", "mag": 12.8},
                    {"band": "Rc", "mag": 12.4},  # not in _BANDS — ignored
                ],
            },
            {
                "auid": "000-BLG-887",
                "ra": "08:51:24.5",
                "dec": "+11:48:02",
                "bands": [{"band": "V", "mag": 13.1}],
            },
        ]
    }
    captured_url = {}

    def fake_get(url, timeout):
        captured_url["url"] = url
        return _FakeResponse(payload)

    monkeypatch.setattr("transform_generator.requests.get", fake_get)

    stars = fetch_standard_field_stars(132.825, 11.8)

    assert "special=std_field" in captured_url["url"]
    assert len(stars) == 2
    assert stars[0]["auid"] == "000-BLG-886"
    assert stars[0]["B"] == 13.5
    assert stars[0]["V"] == 12.8
    assert "R" not in stars[0]  # "Rc" band label not mapped to "R"
    assert stars[1] == {
        "auid": "000-BLG-887", "ra": "08:51:24.5", "dec": "+11:48:02", "V": 13.1,
    }


def test_fetch_standard_field_stars_ignores_bad_mag(monkeypatch):
    payload = {
        "photometry": [
            {
                "auid": "000-XXX-001", "ra": "00:00:00", "dec": "+00:00:00",
                "bands": [{"band": "V", "mag": "n/a"}],
            },
        ]
    }
    monkeypatch.setattr(
        "transform_generator.requests.get",
        lambda url, timeout: _FakeResponse(payload),
    )
    stars = fetch_standard_field_stars(0.0, 0.0)
    assert stars == [{"auid": "000-XXX-001", "ra": "00:00:00", "dec": "+00:00:00"}]


# ── run_transform_generation error paths ─────────────────────────────────────

def test_run_transform_generation_unknown_field_raises():
    with pytest.raises(ValueError, match="Unknown standard field"):
        run_transform_generation("Not A Field", {"V": "dummy.fit"})


def test_run_transform_generation_too_few_reference_stars(monkeypatch):
    monkeypatch.setattr(
        "transform_generator.fetch_standard_field_stars",
        lambda ra, dec: [{"auid": "only-one"}],
    )
    with pytest.raises(ValueError, match="Fewer than 3"):
        run_transform_generation("M67", {"V": "dummy.fit"})
