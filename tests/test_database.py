"""Tests for database.py — SQLite plan persistence round-trips."""
from __future__ import annotations

import pytest

from database import Database
from models import AAVSOTarget, ObservingTarget


@pytest.fixture
def db() -> Database:
    return Database(":memory:")


def _target(name: str, ra: float = 100.0, **aavso_kwargs) -> ObservingTarget:
    return ObservingTarget(
        aavso=AAVSOTarget(star_name=name, ra=ra, dec=45.0, **aavso_kwargs)
    )


def test_empty_database(db):
    assert db.load_plan() == []
    assert db.target_count() == 0


def test_round_trip_preserves_all_fields(db):
    original = ObservingTarget(
        aavso=AAVSOTarget(
            star_name="SS Cyg",
            ra=325.678,
            dec=43.586,
            var_type="UGSS",
            min_mag=12.4,
            min_mag_band="V",
            max_mag=7.7,
            max_mag_band="V",
            period=49.5,
            obs_cadence=1.0,
            obs_mode="CCD",
            obs_section=["Cataclysmic Variables", "Alerts / Campaigns"],
            filters="B,V",
            other_info="campaign notes",
            last_data_point=2460000,
            priority=True,
            constellation="Cyg",
            solar_conjunction=True,
        ),
        script_filters="V,B",
        script_counts="2,2",
        script_intervals="60,60",
        script_binning="2,2",
    )
    db.save_plan([original])
    loaded = db.load_plan()
    assert len(loaded) == 1
    assert loaded[0] == original  # dataclass equality covers every field


def test_round_trip_none_optionals(db):
    db.save_plan([_target("Minimal")])
    t = db.load_plan()[0].aavso
    assert t.min_mag is None
    assert t.max_mag is None
    assert t.period is None
    assert t.obs_section == []


def test_order_preserved(db):
    names = ["C", "A", "B"]  # deliberately not sorted
    db.save_plan([_target(n) for n in names])
    assert [ot.aavso.star_name for ot in db.load_plan()] == names


def test_save_replaces_existing_plan(db):
    db.save_plan([_target("Old1"), _target("Old2")])
    db.save_plan([_target("New")])
    loaded = db.load_plan()
    assert [ot.aavso.star_name for ot in loaded] == ["New"]
    assert db.target_count() == 1


def test_save_empty_plan_clears_database(db):
    db.save_plan([_target("X")])
    db.save_plan([])
    assert db.target_count() == 0


def test_file_database_persists_across_connections(tmp_path):
    path = str(tmp_path / "plan.db")
    first = Database(path)
    first.save_plan([_target("Persisted")])
    del first

    second = Database(path)
    assert [ot.aavso.star_name for ot in second.load_plan()] == ["Persisted"]
