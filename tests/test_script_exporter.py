"""Tests for script_exporter.py — ACP plan generation and validation."""
from __future__ import annotations

import pytest

from models import AAVSOTarget, ObservingTarget
from script_exporter import ScriptExporter


def _target(name: str, ra: float, dec: float = 10.0, **script) -> ObservingTarget:
    return ObservingTarget(
        aavso=AAVSOTarget(star_name=name, ra=ra, dec=dec), **script
    )


@pytest.fixture
def exporter() -> ScriptExporter:
    return ScriptExporter()


def test_targets_sorted_by_ra(exporter):
    script = exporter.generate(
        [_target("West", 300.0), _target("East", 30.0), _target("Mid", 150.0)]
    )
    names = [ln.split("\t")[0] for ln in script.splitlines() if "\t" in ln]
    assert names == ["East", "Mid", "West"]


def test_target_line_format(exporter):
    # RA 353.4165 deg → 23.5611 h; values written tab-separated to 10 decimals
    script = exporter.generate([_target("Z And", 353.41625, 48.8183055556)])
    line = next(ln for ln in script.splitlines() if "\t" in ln)
    name, ra_h, dec = line.split("\t")
    assert name == "Z And"
    assert float(ra_h) == pytest.approx(353.41625 / 15.0)
    assert float(dec) == pytest.approx(48.8183055556)


def test_per_target_directives_precede_target_line(exporter):
    script = exporter.generate(
        [
            _target(
                "SS Cyg",
                325.0,
                script_filters="V,B",
                script_counts="2,3",
                script_intervals="60,90",
                script_binning="1,2",
            )
        ]
    )
    lines = script.splitlines()
    idx = next(i for i, ln in enumerate(lines) if ln.startswith("SS Cyg"))
    assert lines[idx - 4 : idx] == [
        "#filter V,B",
        "#count 2,3",
        "#interval 60,90",
        "#binning 1,2",
    ]


def test_global_directives_at_top(exporter):
    script = exporter.generate(
        [_target("X", 10.0)], defocus=True, vphot=True, platesolve=True, filteroffsets=True
    )
    lines = script.splitlines()
    assert lines[:4] == ["#defocus", "#vphot", "#platesolve", "#filteroffsets"]
    assert lines[4] == ""  # blank separator before first target block


def test_no_global_directives_when_all_disabled(exporter):
    script = exporter.generate([_target("X", 10.0)])
    assert script.splitlines()[0] == "#filter V,B,I"


def test_empty_plan_produces_empty_script(exporter):
    assert exporter.generate([]) == ""


def test_validate_accepts_matching_lengths(exporter):
    assert exporter.validate([_target("OK", 1.0)]) == []


def test_validate_flags_mismatched_lengths(exporter):
    warnings = exporter.validate(
        [_target("Bad", 1.0, script_filters="V,B", script_counts="4,4,4")]
    )
    assert len(warnings) == 1
    assert "Bad" in warnings[0]
    assert "same" in warnings[0]


def test_validate_flags_empty_field(exporter):
    warnings = exporter.validate([_target("Empty", 1.0, script_filters="")])
    assert any("filter is empty" in w for w in warnings)
