"""Persistent application settings via QSettings."""
from __future__ import annotations

import json
import os

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
    def selected_sections(self) -> list[str]:
        v = self._s.value("api/sections", ["ac"])
        if isinstance(v, list):
            return v
        return [v] if v else ["ac"]

    @selected_sections.setter
    def selected_sections(self, v: list[str]) -> None:
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

    # ── SFTP / image download ─────────────────────────────────────────────────

    @property
    def download_protocol(self) -> str:
        """'FTP' or 'SFTP'."""
        return self._s.value("sftp/protocol", "FTP")

    @download_protocol.setter
    def download_protocol(self, v: str) -> None:
        self._s.setValue("sftp/protocol", v)

    @property
    def sftp_host(self) -> str:
        return self._s.value("sftp/host", "data.itelescope.net")

    @sftp_host.setter
    def sftp_host(self, v: str) -> None:
        self._s.setValue("sftp/host", v)

    @property
    def sftp_port(self) -> int:
        return int(self._s.value("sftp/port", 22))

    @sftp_port.setter
    def sftp_port(self, v: int) -> None:
        self._s.setValue("sftp/port", int(v))

    @property
    def sftp_username(self) -> str:
        return self._s.value("sftp/username", "")

    @sftp_username.setter
    def sftp_username(self, v: str) -> None:
        self._s.setValue("sftp/username", v)

    @property
    def sftp_password(self) -> str:
        # NOTE: stored in plaintext via QSettings.  For higher security,
        # migrate to the OS keychain (keyring library) in a future update.
        return self._s.value("sftp/password", "")

    @sftp_password.setter
    def sftp_password(self, v: str) -> None:
        self._s.setValue("sftp/password", v)

    @property
    def sftp_download_path(self) -> str:
        import os
        default = os.path.join(os.path.expanduser("~"), "Incoming")
        return self._s.value("sftp/download_path", default)

    @sftp_download_path.setter
    def sftp_download_path(self, v: str) -> None:
        self._s.setValue("sftp/download_path", v)

    @property
    def sftp_delete_after(self) -> bool:
        return self._s.value("sftp/delete_after", False, type=bool)

    @sftp_delete_after.setter
    def sftp_delete_after(self, v: bool) -> None:
        self._s.setValue("sftp/delete_after", v)

    # ── Image analysis ─────────────────────────────────────────────────────

    @property
    def analysis_working_dir(self) -> str:
        return self._s.value("analysis/working_dir", "")

    @analysis_working_dir.setter
    def analysis_working_dir(self, v: str) -> None:
        self._s.setValue("analysis/working_dir", v)

    @property
    def astap_path(self) -> str:
        default = r"C:\Program Files\astap\astap.exe" if os.name == "nt" else "/usr/bin/astap"
        return self._s.value("analysis/astap_path", default)

    @astap_path.setter
    def astap_path(self, v: str) -> None:
        self._s.setValue("analysis/astap_path", v)

    # ── Exposure calibrations (per telescope + filter, JSON-encoded) ─────────

    def save_exposure_calibration(self, calib_dict: dict) -> None:
        """Store one calibration keyed by telescope + filter (overwrites)."""
        key = f"expcal/{calib_dict['telescope']}_{calib_dict['filter_band']}"
        self._s.setValue(key, json.dumps(calib_dict))

    def load_exposure_calibration(self, telescope: str, filter_band: str) -> dict | None:
        raw = self._s.value(f"expcal/{telescope}_{filter_band}", "")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    def exposure_calibrations(self) -> list[dict]:
        """Return every stored calibration."""
        self._s.beginGroup("expcal")
        try:
            keys = self._s.childKeys()
            out = []
            for k in keys:
                try:
                    out.append(json.loads(self._s.value(k, "")))
                except (TypeError, ValueError):
                    continue
            return out
        finally:
            self._s.endGroup()

    def sync(self) -> None:
        self._s.sync()
