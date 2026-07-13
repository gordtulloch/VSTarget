"""Exposure calculator calibrated from the user's own analyzed images.

Calibration
-----------
:func:`calibrate_from_image` measures a plate-solved, calibrated FITS image:
AAVSO VSP comparison stars in the field are matched to extracted sources and
aperture-photometered, yielding an empirical system model per telescope and
filter:

* ``zeropoint``  – magnitude of a star producing 1 ADU/s in the aperture
* ``sky_rate``   – sky background in ADU/s per pixel
* ``peak_frac``  – fraction of the aperture flux landing in the peak pixel
                   (drives the saturation estimate)

Suggestion
----------
:func:`suggest_exposure` sizes the exposure so the target reaches a requested
S/N at its *faint* end (``min_mag``), subject to a hard cap: the brightest
usable comparison star must never saturate (ensemble photometry dies without
comps).  If the target's *bright* end (``max_mag``) would saturate at the
chosen exposure, a warning is attached rather than shortening the exposure —
the star may well not be in outburst.

The S/N model is photon statistics in ADU with an assumed gain of 1 e-/ADU
and no read-noise term; both simplifications are absorbed into the empirical
calibration well enough for planning purposes.
"""
from __future__ import annotations

import datetime
import logging
import math
import re
from dataclasses import asdict, dataclass, field

import numpy as np
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

# Standard iTelescope-friendly exposure steps (seconds)
EXPOSURE_LADDER: tuple[int, ...] = (10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 300, 600)

DEFAULT_TARGET_SNR = 50.0
DEFAULT_SATURATION_ADU = 55_000.0   # conservative for 16-bit cameras
DEFAULT_COMP_BRIGHT_MAG = 11.0      # brightest comp star that must stay linear
DEFAULT_MAX_EXPOSURE = 300.0
_MATCH_TOLERANCE_PX = 5.0


# ── Calibration model ─────────────────────────────────────────────────────────

@dataclass
class ExposureCalibration:
    """Empirical throughput model for one telescope + filter combination."""

    telescope: str
    filter_band: str
    zeropoint: float          # mag giving 1 ADU/s total aperture flux
    sky_rate: float           # ADU/s per pixel
    peak_frac: float          # peak-pixel fraction of aperture flux
    aperture_radius: float
    n_stars: int
    zp_rms: float             # scatter of per-star zeropoint estimates (mag)
    source_image: str = ""
    date: str = ""

    def flux_rate(self, mag: float) -> float:
        """Total aperture flux in ADU/s for a star of magnitude *mag*."""
        return 10.0 ** (0.4 * (self.zeropoint - mag))

    def snr(self, mag: float, exposure_s: float) -> float:
        """Estimated S/N for *mag* at *exposure_s* (gain 1, no read noise)."""
        signal = self.flux_rate(mag) * exposure_s
        npix = math.pi * self.aperture_radius**2
        noise = math.sqrt(signal + npix * self.sky_rate * exposure_s)
        return signal / noise if noise > 0 else 0.0

    def exposure_for_snr(self, mag: float, target_snr: float) -> float:
        """Exposure (s) needed for *mag* to reach *target_snr*."""
        rate = self.flux_rate(mag)
        npix = math.pi * self.aperture_radius**2
        return target_snr**2 * (rate + npix * self.sky_rate) / rate**2

    def saturation_exposure(
        self, mag: float, saturation_adu: float = DEFAULT_SATURATION_ADU
    ) -> float:
        """Longest exposure (s) before a star of *mag* saturates its peak pixel."""
        peak_rate = self.peak_frac * self.flux_rate(mag) + self.sky_rate
        return saturation_adu / peak_rate if peak_rate > 0 else math.inf

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ExposureCalibration:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def normalize_telescope(name: str) -> str:
    """Reduce telescope descriptions to a short code ('T5').

    Handles 'T05', 'T5 – NMS…', and header values like 'iTelescope 5'
    (the TELESCOP keyword iTelescope writes has no 'T' prefix).
    """
    name = name or ""
    m = re.search(r"\bT0*(\d+)\b", name, re.IGNORECASE)
    if not m:
        m = re.search(r"itelescope\D*0*(\d+)", name, re.IGNORECASE)
    return f"T{m.group(1)}" if m else name.strip() or "UNKNOWN"


# ── Zeropoint fit (pure, unit-testable) ──────────────────────────────────────

def fit_zeropoint(
    catalog_mags: list[float],
    aperture_fluxes: list[float],
    exptime: float,
) -> tuple[float, float, int]:
    """Fit the photometric zeropoint from matched comparison stars.

    Returns ``(zeropoint, rms, n_used)`` where zeropoint is the median of the
    per-star estimates ``mag + 2.5 log10(flux / exptime)``.
    """
    if exptime <= 0:
        raise ValueError(f"exposure time must be positive (got {exptime})")
    zps = [
        m + 2.5 * math.log10(f / exptime)
        for m, f in zip(catalog_mags, aperture_fluxes, strict=True)
        if f > 0
    ]
    if not zps:
        raise ValueError("no comparison stars with positive flux")
    zp = float(np.median(zps))
    rms = float(np.std(zps)) if len(zps) > 1 else 0.0
    return zp, rms, len(zps)


# ── Exposure suggestion ───────────────────────────────────────────────────────

@dataclass
class ExposureSuggestion:
    """Result of one exposure suggestion."""

    seconds: int
    expected_snr: float
    capped_by_comp: bool          # comp-star saturation limited the exposure
    target_may_saturate: bool     # bright end (max_mag) would saturate
    warnings: list[str] = field(default_factory=list)


def suggest_exposure(
    calib: ExposureCalibration,
    faint_mag: float,
    bright_mag: float | None = None,
    target_snr: float = DEFAULT_TARGET_SNR,
    comp_bright_mag: float = DEFAULT_COMP_BRIGHT_MAG,
    saturation_adu: float = DEFAULT_SATURATION_ADU,
    max_exposure: float = DEFAULT_MAX_EXPOSURE,
    ladder: tuple[int, ...] = EXPOSURE_LADDER,
) -> ExposureSuggestion:
    """Suggest an exposure sized for *faint_mag* at *target_snr*.

    Hard constraints (never exceeded):
      * the brightest comparison star (*comp_bright_mag*) must not saturate
      * *max_exposure*

    Soft constraint (warning only): the target at *bright_mag* should not
    saturate — variables may be observed at any brightness between the two.
    """
    warnings: list[str] = []

    t_snr = calib.exposure_for_snr(faint_mag, target_snr)
    t_comp = calib.saturation_exposure(comp_bright_mag, saturation_adu)
    cap = min(t_comp, max_exposure)

    capped = t_snr > cap
    if t_snr > t_comp and t_comp <= max_exposure:
        warnings.append(
            f"S/N {target_snr:.0f} at mag {faint_mag:.1f} needs {t_snr:.0f}s but "
            f"comparison stars (mag ≤ {comp_bright_mag:.1f}) saturate beyond "
            f"{t_comp:.0f}s — consider more exposures (Count) instead."
        )
    elif capped:
        warnings.append(
            f"S/N {target_snr:.0f} at mag {faint_mag:.1f} needs {t_snr:.0f}s; "
            f"clamped to the {max_exposure:.0f}s maximum — consider more "
            f"exposures (Count) instead."
        )

    # Snap to the ladder without ever exceeding the cap
    allowed = [s for s in ladder if s <= cap] or [max(1, int(cap))]
    at_least_snr = [s for s in allowed if s >= t_snr]
    seconds = min(at_least_snr) if at_least_snr else max(allowed)

    target_sat = False
    if bright_mag is not None:
        t_target_sat = calib.saturation_exposure(bright_mag, saturation_adu)
        if seconds > t_target_sat:
            target_sat = True
            warnings.append(
                f"Target at its bright end (mag {bright_mag:.1f}) saturates "
                f"beyond {t_target_sat:.0f}s — check recent brightness before "
                f"using {seconds}s."
            )

    return ExposureSuggestion(
        seconds=seconds,
        expected_snr=calib.snr(faint_mag, seconds),
        capped_by_comp=capped,
        target_may_saturate=target_sat,
        warnings=warnings,
    )


# ── Calibration from a FITS image ────────────────────────────────────────────

def calibrate_from_image(
    fits_path: str,
    aperture_radius: float = 6.0,
    snr_threshold: float = 5.0,
    progress: object | None = None,
) -> ExposureCalibration:
    """Measure an :class:`ExposureCalibration` from a plate-solved image.

    Requires a celestial WCS and an EXPTIME/EXPOSURE header.  Comparison-star
    magnitudes come from the AAVSO VSP chart for the field centre.
    """
    import os

    from astropy.io import fits
    from astropy.coordinates import SkyCoord
    from astropy.wcs import WCS
    import astropy.units as u
    from photutils.aperture import CircularAperture, aperture_photometry

    from photometry import download_comp_stars, extract_sources

    def _log(msg: str) -> None:
        logger.info("%s", msg)
        if progress:
            progress(msg)

    _log(f"Calibrating from {os.path.basename(fits_path)}…")
    with fits.open(fits_path) as hdul:
        data = hdul[0].data.astype(np.float32)
        header = hdul[0].header.copy()

    wcs = WCS(header)
    if not wcs.has_celestial:
        raise ValueError("Image has no celestial WCS — plate-solve it first.")

    exptime = float(header.get("EXPTIME", 0) or header.get("EXPOSURE", 0) or 0)
    if exptime <= 0:
        raise ValueError("No EXPTIME/EXPOSURE in FITS header.")

    filter_band = str(header.get("FILTER", "V")).strip() or "V"
    telescope = normalize_telescope(
        str(header.get("TELESCOP", "")) or os.path.basename(fits_path)
    )

    # Field centre → VSP chart
    cy, cx = np.array(data.shape) / 2
    centre = wcs.pixel_to_world(cx, cy)
    ra_str = centre.ra.to_string(unit=u.hour, sep=" ", precision=2)
    dec_str = centre.dec.to_string(unit=u.deg, sep=" ", precision=1, alwayssign=False)

    _log(f"Downloading VSP comparison stars ({filter_band}) for field centre…")
    comp_stars, _chart = download_comp_stars(ra_str, dec_str, filter_band)
    if not comp_stars:
        raise ValueError(f"No VSP comparison stars for this field in {filter_band}.")

    _log(f"Extracting sources (SNR ≥ {snr_threshold:g})…")
    sources, back_arr = extract_sources(data, snr_threshold)
    if len(sources) == 0:
        raise ValueError("No sources extracted from the image.")
    data_sub = data - back_arr

    # Match comps to extracted sources via WCS
    matched: list[dict] = []
    for comp in comp_stars:
        try:
            sky = SkyCoord(comp["ra"], comp["dec"], unit=(u.hourangle, u.deg))
            xy = SkyCoord.to_pixel(sky, wcs=wcs, origin=1)
            px, py = float(xy[0]), float(xy[1])
        except Exception:
            continue
        for src in sources:
            if (
                abs(float(src["x"]) - px) < _MATCH_TOLERANCE_PX
                and abs(float(src["y"]) - py) < _MATCH_TOLERANCE_PX
            ):
                matched.append(
                    {"vmag": comp["vmag"], "x": px, "y": py, "peak": float(src["peak"])}
                )
                break

    if len(matched) < 3:
        raise ValueError(
            f"Only {len(matched)} comparison star(s) matched — need at least 3. "
            "Check the plate solve and try a lower SNR threshold."
        )
    _log(f"Matched {len(matched)} comparison stars; measuring…")

    positions = [(m["x"], m["y"]) for m in matched]
    apertures = CircularAperture(positions, r=aperture_radius)
    phot = aperture_photometry(data_sub.astype(np.float64), apertures)
    fluxes = [float(v) for v in phot["aperture_sum"]]

    zp, rms, n_used = fit_zeropoint([m["vmag"] for m in matched], fluxes, exptime)

    peak_fracs = [
        m["peak"] / f for m, f in zip(matched, fluxes, strict=True) if f > 0
    ]
    peak_frac = float(np.median(peak_fracs))
    sky_rate = float(np.median(back_arr)) / exptime

    calib = ExposureCalibration(
        telescope=telescope,
        filter_band=filter_band,
        zeropoint=zp,
        sky_rate=sky_rate,
        peak_frac=peak_frac,
        aperture_radius=aperture_radius,
        n_stars=n_used,
        zp_rms=rms,
        source_image=os.path.basename(fits_path),
        date=datetime.datetime.now().isoformat(timespec="seconds"),
    )
    _log(
        f"Calibrated {telescope}/{filter_band}: ZP={zp:.2f} ±{rms:.2f} "
        f"({n_used} stars), sky {sky_rate:.2f} ADU/s/px"
    )
    return calib


# ── Background thread ─────────────────────────────────────────────────────────

class CalibrationThread(QThread):
    """Run :func:`calibrate_from_image` in a background thread.

    Signals
    -------
    status(str)          – Progress messages.
    succeeded(object)    – ExposureCalibration on success.
    error(str)           – Error message on failure.
    """

    status = Signal(str)
    succeeded = Signal(object)
    error = Signal(str)

    def __init__(self, fits_path: str, parent=None) -> None:
        super().__init__(parent)
        self._fits_path = fits_path

    def run(self) -> None:
        try:
            calib = calibrate_from_image(self._fits_path, progress=self.status.emit)
            self.succeeded.emit(calib)
        except Exception as exc:
            logger.error("Exposure calibration failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))
