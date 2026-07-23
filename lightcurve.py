"""AAVSO International Database (AID) light-curve fetch.

Uses the delimited API that also powers AAVSO's own VSX light-curve plots
(``https://vsx.aavso.org/index.php?view=api.delim``), filtered by star name,
Julian Date range, and photometric band.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

_AID_URL = "https://vsx.aavso.org/index.php"
_DELIM = "@$@"


@dataclass
class LightCurvePoint:
    jd:       float
    mag:      float
    uncert:   float | None
    band:     str
    observer: str


def fetch_observations(
    star_name: str,
    from_jd: float,
    to_jd: float,
    filter_band: str | None = None,
) -> list[LightCurvePoint]:
    """Download AID observations for *star_name* between *from_jd* and *to_jd*.

    When *filter_band* is given, only observations in that exact band are
    kept (e.g. ``"V"``); fainter-than (non-detection) rows are always
    excluded since they are not real magnitude measurements.
    """
    params = {
        "view": "api.delim",
        "ident": star_name,
        "fromjd": f"{from_jd:.5f}",
        "tojd": f"{to_jd:.5f}",
    }
    logger.info("AID request: %s", params)
    resp = requests.get(_AID_URL, params=params, timeout=30)
    resp.raise_for_status()
    lines = resp.text.strip().splitlines()
    if len(lines) < 2:
        return []

    header = lines[0].split(_DELIM)
    points: list[LightCurvePoint] = []
    for line in lines[1:]:
        fields = line.split(_DELIM)
        if len(fields) != len(header):
            continue
        row = dict(zip(header, fields, strict=True))

        if row.get("fainterThan") == "1":
            continue
        band = row.get("band", "").strip()
        if filter_band and band.lower() != filter_band.strip().lower():
            continue
        try:
            jd = float(row["JD"])
            mag = float(row["mag"])
        except (KeyError, ValueError):
            continue
        try:
            uncert = float(row["uncert"]) if row.get("uncert") else None
        except ValueError:
            uncert = None

        points.append(LightCurvePoint(
            jd=jd, mag=mag, uncert=uncert, band=band,
            observer=row.get("by", ""),
        ))

    points.sort(key=lambda p: p.jd)
    logger.info(
        "AID: %d observations for '%s' (%s) between JD %.3f-%.3f",
        len(points), star_name, filter_band or "any", from_jd, to_jd,
    )
    return points


class LightCurveThread(QThread):
    """Background fetch of AAVSO light-curve data for the report chart.

    Signals
    -------
    finished(list)  – list[LightCurvePoint] on success (may be empty).
    error(str)      – error message on failure.
    """

    finished = Signal(list)
    error    = Signal(str)

    def __init__(
        self,
        star_name: str,
        filter_band: str,
        from_jd: float,
        to_jd: float,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._star_name   = star_name
        self._filter_band = filter_band
        self._from_jd     = from_jd
        self._to_jd       = to_jd

    def run(self) -> None:
        try:
            points = fetch_observations(
                self._star_name, self._from_jd, self._to_jd, self._filter_band
            )
            self.finished.emit(points)
        except Exception as exc:  # noqa: BLE001
            logger.error("AAVSO light curve fetch failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))
