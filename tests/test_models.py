"""Tests for models.py — AAVSOTarget construction and conversions."""
from __future__ import annotations

import pytest

from models import SECTION_CODES, SECTION_NAMES, AAVSOTarget


def test_from_api_full_record():
    data = {
        "star_name": "SS Cyg",
        "ra": 325.678,
        "dec": 43.586,
        "var_type": "UGSS",
        "min_mag": 12.4,
        "min_mag_band": "V",
        "max_mag": 7.7,
        "max_mag_band": "V",
        "period": 49.5,
        "obs_cadence": 1.0,
        "obs_mode": "CCD",
        "obs_section": ["Cataclysmic Variables"],
        "filter": "B,V",  # API key is 'filter', model field is 'filters'
        "other_info": "notes",
        "last_data_point": 2460000,
        "priority": True,
        "constellation": "Cyg",
        "solar_conjunction": False,
    }
    t = AAVSOTarget.from_api(data)
    assert t.star_name == "SS Cyg"
    assert t.ra == pytest.approx(325.678)
    assert t.filters == "B,V"
    assert t.obs_section == ["Cataclysmic Variables"]
    assert t.priority is True
    assert t.solar_conjunction is False


def test_from_api_minimal_record_uses_defaults():
    t = AAVSOTarget.from_api({"star_name": "X", "ra": 15.0, "dec": -10.0})
    assert t.var_type == ""
    assert t.min_mag is None
    assert t.max_mag is None
    assert t.obs_section == []
    assert t.filters == ""
    assert t.priority is False


def test_from_api_none_values_normalized_to_empty_strings():
    # The API returns explicit nulls for some string fields
    data = {
        "star_name": "Y",
        "ra": 0.0,
        "dec": 0.0,
        "min_mag_band": None,
        "max_mag_band": None,
        "obs_mode": None,
        "obs_section": None,
        "filter": None,
        "other_info": None,
        "constellation": None,
    }
    t = AAVSOTarget.from_api(data)
    assert t.min_mag_band == ""
    assert t.obs_mode == ""
    assert t.obs_section == []
    assert t.filters == ""
    assert t.constellation == ""


def test_ra_hours_conversion():
    t = AAVSOTarget(star_name="Z", ra=353.416, dec=48.818)
    assert t.ra_hours == pytest.approx(353.416 / 15.0)


def test_section_maps_are_inverse():
    assert {v: k for k, v in SECTION_CODES.items()} == SECTION_NAMES
    assert len(SECTION_NAMES) == len(SECTION_CODES)  # no duplicate codes
