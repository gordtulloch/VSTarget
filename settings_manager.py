"""Persistent application settings via QSettings."""
from __future__ import annotations

from typing import List

from PySide6.QtCore import QSettings


class SettingsManager:
    """Thin wrapper around QSettings for VSTarget application state."""

    _APP = "VSTarget"
    _ORG = "AAVSO"

    def __init__(self) -> None:
        self._s = QSettings(self._ORG, self._APP)

    # ── API ──────────────────────────────────────────────────────────────────

    @property
    def api_key(self) -> str:
        return self._s.value("api/key", "")

    @api_key.setter
    def api_key(self, v: str) -> None:
        self._s.setValue("api/key", v)

    # ── Telescope / location ─────────────────────────────────────────────────

    @property
    def telescope_name(self) -> str:
        return self._s.value("telescope/name", "T5 – New Mexico Skies (Mayhill, NM)")

    @telescope_name.setter
    def telescope_name(self, v: str) -> None:
        self._s.setValue("telescope/name", v)

    @property
    def latitude(self) -> float:
        return float(self._s.value("telescope/latitude", 32.9025))

    @latitude.setter
    def latitude(self, v: float) -> None:
        self._s.setValue("telescope/latitude", v)

    @property
    def longitude(self) -> float:
        return float(self._s.value("telescope/longitude", -105.5319))

    @longitude.setter
    def longitude(self, v: float) -> None:
        self._s.setValue("telescope/longitude", v)

    @property
    def target_altitude(self) -> float:
        return float(self._s.value("telescope/target_altitude", 20.0))

    @target_altitude.setter
    def target_altitude(self, v: float) -> None:
        self._s.setValue("telescope/target_altitude", v)

    @property
    def sun_altitude(self) -> float:
        return float(self._s.value("telescope/sun_altitude", -5.0))

    @sun_altitude.setter
    def sun_altitude(self, v: float) -> None:
        self._s.setValue("telescope/sun_altitude", v)

    # ── Default script parameters ─────────────────────────────────────────────

    @property
    def default_filters(self) -> str:
        return self._s.value("defaults/filters", "V,B,I")

    @default_filters.setter
    def default_filters(self, v: str) -> None:
        self._s.setValue("defaults/filters", v)

    @property
    def default_counts(self) -> str:
        return self._s.value("defaults/counts", "4,4,4")

    @default_counts.setter
    def default_counts(self, v: str) -> None:
        self._s.setValue("defaults/counts", v)

    @property
    def default_intervals(self) -> str:
        return self._s.value("defaults/intervals", "30,30,30")

    @default_intervals.setter
    def default_intervals(self, v: str) -> None:
        self._s.setValue("defaults/intervals", v)

    @property
    def default_binning(self) -> str:
        return self._s.value("defaults/binning", "1,1,1")

    @default_binning.setter
    def default_binning(self, v: str) -> None:
        self._s.setValue("defaults/binning", v)

    # ── Download section selection ────────────────────────────────────────────

    @property
    def selected_sections(self) -> List[str]:
        v = self._s.value("api/sections", ["ac"])
        if isinstance(v, list):
            return v
        return [v] if v else ["ac"]

    @selected_sections.setter
    def selected_sections(self, v: List[str]) -> None:
        self._s.setValue("api/sections", v)

    # ── Global script directives ──────────────────────────────────────────────

    @property
    def directive_defocus(self) -> bool:
        return self._s.value("directives/defocus", False, type=bool)

    @directive_defocus.setter
    def directive_defocus(self, v: bool) -> None:
        self._s.setValue("directives/defocus", v)

    @property
    def directive_vphot(self) -> bool:
        return self._s.value("directives/vphot", False, type=bool)

    @directive_vphot.setter
    def directive_vphot(self, v: bool) -> None:
        self._s.setValue("directives/vphot", v)

    @property
    def directive_platesolve(self) -> bool:
        return self._s.value("directives/platesolve", False, type=bool)

    @directive_platesolve.setter
    def directive_platesolve(self, v: bool) -> None:
        self._s.setValue("directives/platesolve", v)

    @property
    def directive_filteroffsets(self) -> bool:
        return self._s.value("directives/filteroffsets", False, type=bool)

    @directive_filteroffsets.setter
    def directive_filteroffsets(self, v: bool) -> None:
        self._s.setValue("directives/filteroffsets", v)

    def sync(self) -> None:
        self._s.sync()
