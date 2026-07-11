"""Data models for VSTarget."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


# Maps the obs_section display values returned by the AAVSO API to their
# corresponding query codes used in the /targets endpoint.
SECTION_CODES: Dict[str, str] = {
    "Alerts / Campaigns": "ac",
    "Cataclysmic Variables": "cv",
    "Eclipsing Variables": "eb",
    "Short Period Pulsators": "spp",
    "Long Period Variables": "lpv",
    "Young Stellar Objects": "yso",
    "High Energy Targets": "het",
    "Exoplanets": "ep",
    "Miscellaneous": "misc",
}

# Reverse map: code → display name (used in UI checkboxes)
SECTION_NAMES: Dict[str, str] = {v: k for k, v in SECTION_CODES.items()}


# Pre-configured telescope location presets.
# Coordinates are approximate; users should verify and use "Custom" if needed.
TELESCOPE_PRESETS: Dict[str, Dict[str, float]] = {
    "T5 – New Mexico Skies (Mayhill, NM)": {
        "latitude": 32.9025,
        "longitude": -105.5319,
        "target_altitude": 20.0,
        "sun_altitude": -5.0,
    },
    "T7 – New Mexico Skies (Mayhill, NM)": {
        "latitude": 32.9025,
        "longitude": -105.5319,
        "target_altitude": 20.0,
        "sun_altitude": -5.0,
    },
    "T11 – New Mexico Skies (Mayhill, NM)": {
        "latitude": 32.9025,
        "longitude": -105.5319,
        "target_altitude": 20.0,
        "sun_altitude": -5.0,
    },
    "T24 – New Mexico Skies (Mayhill, NM)": {
        "latitude": 32.9025,
        "longitude": -105.5319,
        "target_altitude": 20.0,
        "sun_altitude": -5.0,
    },
    "T17 – Siding Spring (NSW, Australia)": {
        "latitude": -31.2733,
        "longitude": 149.0644,
        "target_altitude": 20.0,
        "sun_altitude": -5.0,
    },
    "T30 – Siding Spring (NSW, Australia)": {
        "latitude": -31.2733,
        "longitude": 149.0644,
        "target_altitude": 20.0,
        "sun_altitude": -5.0,
    },
    "T21 – Sierra Remote Obs. (Auberry, CA)": {
        "latitude": 37.0703,
        "longitude": -119.4097,
        "target_altitude": 20.0,
        "sun_altitude": -5.0,
    },
    "Custom": {
        "latitude": 0.0,
        "longitude": 0.0,
        "target_altitude": 20.0,
        "sun_altitude": -5.0,
    },
}


@dataclass
class AAVSOTarget:
    """A variable star target from the AAVSO Target Tool API."""

    star_name: str
    ra: float               # decimal degrees (from API)
    dec: float              # decimal degrees (from API)
    var_type: str = ""
    min_mag: Optional[float] = None
    min_mag_band: str = ""
    max_mag: Optional[float] = None
    max_mag_band: str = ""
    period: Optional[float] = None
    obs_cadence: Optional[float] = None
    obs_mode: str = ""
    obs_section: List[str] = field(default_factory=list)
    filters: str = ""       # suggested filters from AAVSO
    other_info: str = ""
    last_data_point: Optional[int] = None
    priority: bool = False
    constellation: str = ""
    solar_conjunction: bool = False

    @property
    def ra_hours(self) -> float:
        """RA in decimal hours (required by iTelescope script format)."""
        return self.ra / 15.0

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "AAVSOTarget":
        """Construct from a single entry in the AAVSO API JSON response."""
        return cls(
            star_name=data.get("star_name", ""),
            ra=float(data.get("ra", 0.0)),
            dec=float(data.get("dec", 0.0)),
            var_type=data.get("var_type", ""),
            min_mag=data.get("min_mag"),
            min_mag_band=data.get("min_mag_band", "") or "",
            max_mag=data.get("max_mag"),
            max_mag_band=data.get("max_mag_band", "") or "",
            period=data.get("period"),
            obs_cadence=data.get("obs_cadence"),
            obs_mode=data.get("obs_mode", "") or "",
            obs_section=data.get("obs_section") or [],
            filters=data.get("filter", "") or "",   # API key is 'filter'
            other_info=data.get("other_info", "") or "",
            last_data_point=data.get("last_data_point"),
            priority=bool(data.get("priority", False)),
            constellation=data.get("constellation", "") or "",
            solar_conjunction=bool(data.get("solar_conjunction", False)),
        )


@dataclass
class ObservingTarget:
    """An AAVSO target added to the observation plan with per-target script parameters."""

    aavso: AAVSOTarget
    script_filters: str = "V,B,I"
    script_counts: str = "4,4,4"
    script_intervals: str = "30,30,30"
    script_binning: str = "1,1,1"
