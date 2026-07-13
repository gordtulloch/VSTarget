"""Aperture photometry engine producing AAVSO WebObs Extended-format reports.

Workflow (mirrors notebooks/RWAUR.ipynb):
  1. Read FITS image + WCS
  2. Query Simbad for target coordinates
  3. Download comparison stars from AAVSO VSP API
  4. Extract image sources (SEP, falls back to photutils DAOStarFinder)
  5. Match comparison stars to pixel sources via WCS
  6. Aperture photometry with photutils
  7. Ensemble linear regression → calibrated target magnitude
  8. Format output in AAVSO WebObs Extended format
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.time import Time
from astropy.wcs import WCS
import astropy.units as u
from photutils.aperture import CircularAperture, aperture_photometry
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


# ── Per-run log file writer ──────────────────────────────────────────────

class PhotometryLog:
    """Writes a structured plain-text log file for one analysis run.

    The file is created in *log_dir* (the working directory) and named::

        photometry_YYYYMMDD_HHMMSS_<starname>.log

    Use as a context manager or call :meth:`close` explicitly.
    All writes are also forwarded to *progress_cb* (if supplied) so the
    UI status label stays in sync.
    """

    def __init__(
        self,
        log_dir: Optional[str],
        star_name: str,
        progress_cb: Optional[callable] = None,
    ) -> None:
        self._cb = progress_cb
        self._fh = None
        self.path: Optional[str] = None

        if log_dir:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe = star_name.replace(" ", "").replace("/", "-")
            self.path = os.path.join(log_dir, f"photometry_{ts}_{safe}.log")
            try:
                os.makedirs(log_dir, exist_ok=True)
                self._fh = open(self.path, "w", encoding="utf-8")  # noqa: SIM115
                logger.info("Photometry log: %s", self.path)
            except OSError as exc:
                logger.warning("Cannot open log file %s: %s", self.path, exc)
                self._fh = None

    # ── Output primitives ─────────────────────────────────────────────────────

    def _write(self, text: str) -> None:
        if self._fh:
            self._fh.write(text)
            self._fh.flush()

    def section(self, title: str) -> None:
        """Emit a visually prominent section header."""
        bar = "=" * 70
        text = f"\n{bar}\n  {title}\n{bar}\n"
        self._write(text)
        self._emit_cb(title)

    def line(self, text: str = "") -> None:
        """Emit one log line (also forwards to the UI progress callback)."""
        self._write(text + "\n")
        self._emit_cb(text)

    def detail(self, text: str) -> None:
        """Emit a detail line (written to file only – no UI callback)."""
        self._write("  " + text + "\n")

    def table(self, headers: List[str], rows: List[list], col_width: int = 14) -> None:
        """Write a fixed-width text table."""
        sep = "-" * (col_width * len(headers) + len(headers) + 1)
        header_row = "|".join(h.ljust(col_width) for h in headers)
        self._write(f"  {sep}\n  {header_row}\n  {sep}\n")
        for row in rows:
            row_str = "|".join(str(v).ljust(col_width) for v in row)
            self._write(f"  {row_str}\n")
        self._write(f"  {sep}\n")

    def _emit_cb(self, msg: str) -> None:
        if self._cb and msg.strip():
            self._cb(msg)

    # ── Context manager ───────────────────────────────────────────────────────

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

# ── AAVSO VSP comparison-star API ─────────────────────────────────────────────

_VSP_URL = (
    "https://www.aavso.org/apps/vsp/api/chart/"
    "?format=json&fov={fov}&maglimit=18.5&ra={ra}&dec={dec}"
)


def download_comp_stars(
    ra: str,
    dec: str,
    filter_band: str = "V",
    field_of_view: float = 18.5,
) -> Tuple[List[dict], str]:
    """Download comparison-star data from AAVSO VSP.

    Returns ``(stars, chart_id)`` where *stars* is a list of dicts with keys
    ``auid``, ``ra``, ``dec``, ``vmag``, ``error``.
    """
    url = _VSP_URL.format(fov=field_of_view, ra=ra, dec=dec)
    logger.info("VSP request: %s", url)
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    chart_id = str(data.get("chartid", "na"))
    stars: List[dict] = []
    for star in data.get("photometry", []):
        comp: dict = {"auid": star["auid"], "ra": star["ra"], "dec": star["dec"]}
        for band in star.get("bands", []):
            if band["band"] == filter_band:
                try:
                    comp["vmag"]  = float(band["mag"])
                    comp["error"] = float(band.get("error") or 0.0)
                except (TypeError, ValueError):
                    pass
                break
        if "vmag" in comp:
            stars.append(comp)
    logger.info("Downloaded %d comparison stars (chart %s)", len(stars), chart_id)
    return stars, chart_id


# ── Simbad coordinate lookup ──────────────────────────────────────────────────

def lookup_simbad(star_name: str) -> Tuple[str, str]:
    """Return ``(RA_str, DEC_str)`` for *star_name* via astroquery Simbad."""
    from astroquery.simbad import Simbad  # type: ignore

    result = Simbad.query_object(star_name)
    if result is None or len(result) == 0:
        raise ValueError(f"Simbad returned no result for '{star_name}'")
    ra  = str(result[0]["RA"])
    dec = str(result[0]["DEC"]).replace("+", "").replace("-", "")
    return ra, dec


# ── Source extraction ─────────────────────────────────────────────────────────

def extract_sources(
    data: np.ndarray,
    snr: float = 5.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """Extract point sources.  Returns ``(sources, background_2d_array)``.

    Uses SEP when available; falls back to photutils DAOStarFinder.
    The returned ``sources`` array has at least ``x`` and ``y`` fields.
    """
    try:
        import sep  # type: ignore

        bkg = sep.Background(data.astype(np.float32))
        back_arr = bkg.back()
        data_sub = data - back_arr
        objects = sep.extract(data_sub, snr * bkg.globalrms)
        logger.info("SEP: extracted %d sources", len(objects))
        return objects, back_arr

    except ImportError:
        from photutils.background import Background2D, MedianBackground
        from photutils.detection import DAOStarFinder

        bkg2d = Background2D(
            data, (50, 50), filter_size=(3, 3),
            bkg_estimator=MedianBackground(),
        )
        back_arr = bkg2d.background
        data_sub = data - back_arr
        daofind = DAOStarFinder(fwhm=3.0, threshold=snr * float(bkg2d.background_rms_median))
        sources = daofind(data_sub)
        if sources is None or len(sources) == 0:
            return np.array([], dtype=[("x", "f4"), ("y", "f4"), ("peak", "f4")]), back_arr
        dtype = np.dtype([("x", np.float32), ("y", np.float32), ("peak", np.float32)])
        arr = np.zeros(len(sources), dtype=dtype)
        arr["x"]    = sources["xcentroid"].data
        arr["y"]    = sources["ycentroid"].data
        arr["peak"] = sources["peak"].data
        logger.info("DAOStarFinder: extracted %d sources", len(arr))
        return arr, back_arr


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class PhotometryResult:
    """All outputs from one photometric analysis run."""
    star_name:          str
    fits_path:          str
    filter_band:        str
    obs_date:           datetime
    jd:                 float
    target_mag:         float
    mag_error:          float
    chart_id:           str
    comp_star:          str     # AUID of primary comparison star
    comp_mag:           float
    check_star:         str     # AUID of check star
    check_mag_measured: float
    check_mag_catalog:  float
    airmass:            float
    ensemble_stars:     List[str] = field(default_factory=list)
    notes:              str = ""
    observer_code:      str = "UNKNOWN"


# ── Main analysis function ────────────────────────────────────────────────────

def run_photometry(
    fits_path: str,
    star_name: Optional[str]  = None,
    filter_band: Optional[str] = None,
    comp_mag_min: float = 11.0,
    comp_mag_max: float = 13.5,
    aperture_radius: float = 6.0,
    snr_threshold: float  = 5.0,
    observer_code: str    = "UNKNOWN",
    progress: Optional[callable] = None,
    log_dir: Optional[str] = None,
) -> PhotometryResult:
    """Run the full aperture-photometry pipeline and return a result.

    A plain-text log is written to *log_dir* when supplied, recording each
    step with tables matching the notebook output.
    """
    with PhotometryLog(log_dir, star_name or "UNKNOWN", progress) as log:

        # ── 1. Read FITS ──────────────────────────────────────────────────────
        log.section("Step 1 – Read FITS image")
        log.line(f"File : {fits_path}")
        with fits.open(fits_path) as hdul:
            raw_data = hdul[0].data.astype(np.float32)
            header   = hdul[0].header.copy()

        wcs = WCS(header)

        if star_name is None:
            star_name = str(header.get("OBJECT", "UNKNOWN")).strip()
        if filter_band is None:
            filter_band = str(header.get("FILTER", "V")).strip() or "V"

        date_str = str(header.get("DATE-OBS", ""))
        try:
            t = Time(date_str, format="isot")
            obs_date = t.to_datetime(timezone=timezone.utc)
            jd = float(t.jd)
        except Exception:
            obs_date = datetime.now(timezone.utc)
            jd = float(Time(obs_date).jd)

        airmass  = float(header.get("AIRMASS", 0.0) or 0.0)
        img_h, img_w = raw_data.shape

        log.detail(f"Star name  : {star_name}")
        log.detail(f"Filter     : {filter_band}")
        log.detail(f"DATE-OBS   : {date_str}")
        log.detail(f"Julian Day : {jd:.5f}")
        log.detail(f"Airmass    : {airmass:.3f}")
        log.detail(f"Dimensions : {img_w} x {img_h} px")
        log.detail(f"Image mean : {float(raw_data.mean()):.1f}  "
                   f"std: {float(raw_data.std()):.1f}  "
                   f"max: {float(raw_data.max()):.1f}")

        # ── 2. Resolve target coordinates ─────────────────────────────────────
        log.section("Step 2 – Resolve target coordinates (Simbad)")
        try:
            target_ra, target_dec = lookup_simbad(star_name)
            log.line(f"Simbad: RA = {target_ra}  Dec = {target_dec}")
        except Exception as exc:
            log.line(f"Simbad lookup failed ({exc}); using WCS field centre")
            cy, cx = np.array(raw_data.shape) / 2
            sky = wcs.pixel_to_world(cx, cy)
            target_ra  = sky.ra.to_string(unit=u.hour, sep=" ", precision=2)
            target_dec = sky.dec.to_string(unit=u.deg,  sep=" ", precision=1, alwayssign=False)
            log.detail(f"Fallback RA = {target_ra}  Dec = {target_dec}")

        # ── 3. Comparison stars ───────────────────────────────────────────────
        log.section("Step 3 – Download AAVSO comparison stars")
        comp_stars, chart_id = download_comp_stars(target_ra, target_dec, filter_band)
        if not comp_stars:
            raise ValueError(
                f"No comparison stars from AAVSO VSP for '{star_name}' / {filter_band}."
            )
        log.line(f"Chart ID   : {chart_id}")
        log.line(f"Downloaded : {len(comp_stars)} comparison stars in {filter_band}")
        log.table(
            ["AUID", "RA", "Dec", f"{filter_band} mag", "error"],
            [[s["auid"], s["ra"], s["dec"],
              f"{s['vmag']:.3f}", f"{s.get('error', 0):.3f}"]
             for s in comp_stars],
        )

        # ── 4. Source extraction ──────────────────────────────────────────────
        log.section(f"Step 4 – Extract image sources  (SNR ≥ {snr_threshold})")
        sources, back_arr = extract_sources(raw_data, snr_threshold)
        data_sub = raw_data - back_arr
        log.line(f"Sources found     : {len(sources)}")
        log.detail(f"Background median : {float(back_arr.mean()):.2f}")
        if len(sources):
            peaks = [float(s["peak"]) for s in sources]
            log.detail(f"Source peak range : {min(peaks):.0f} – {max(peaks):.0f} ADU")

        # ── 5. Match to pixel positions ───────────────────────────────────────
        log.section("Step 5 – Match comparison stars to image sources")
        all_stars = comp_stars + [{"auid": "target", "ra": target_ra, "dec": target_dec}]

        for star in all_stars:
            try:
                sky = SkyCoord(star["ra"], star["dec"], unit=(u.hourangle, u.deg))
                xy  = SkyCoord.to_pixel(sky, wcs=wcs, origin=1)
                px, py = float(xy[0]), float(xy[1])
            except Exception:
                star["x"] = 0.0
                star["y"] = 0.0
                continue
            matched = False
            for src in sources:
                if abs(float(src["x"]) - px) < 5 and abs(float(src["y"]) - py) < 5:
                    star["x"]    = px
                    star["y"]    = py
                    star["peak"] = float(src["peak"])
                    matched = True
                    break
            if not matched:
                star["x"] = 0.0
                star["y"] = 0.0

        df = pd.DataFrame(all_stars)
        df = df[df["x"] > 0].copy()
        matched_ids = list(df["auid"].values)
        unmatched   = [s["auid"] for s in all_stars
                       if s["auid"] not in matched_ids and s["auid"] != "target"]

        log.line(f"Matched   : {len(df)} stars (including target)")
        if unmatched:
            log.detail(f"Unmatched : {', '.join(unmatched)}")
        log.table(
            ["AUID", "x (px)", "y (px)", "peak (ADU)"],
            [[r["auid"], f"{r['x']:.1f}", f"{r['y']:.1f}", f"{r.get('peak', 0):.0f}"]
             for _, r in df.iterrows()],
        )

        if "target" not in df["auid"].values:
            raise ValueError(
                "Target star not detected in the image.  "
                "Check that the image is plate-solved and the star is in the field."
            )

        # ── 6. Aperture photometry ────────────────────────────────────────────
        log.section(f"Step 6 – Aperture photometry  (r = {aperture_radius} px)")
        positions = list(zip(df["x"].values, df["y"].values))
        apertures = CircularAperture(positions, r=aperture_radius)
        phot      = aperture_photometry(data_sub.astype(np.float64), apertures)

        df["aperture_sum"]     = phot["aperture_sum"].value
        df["instrumental_mag"] = df["aperture_sum"].apply(
            lambda s: -2.5 * math.log10(max(float(s), 1e-10))
        )
        log.table(
            ["AUID", "aperture_sum", "inst_mag", "vmag (cat)"],
            [[r["auid"],
              f"{r['aperture_sum']:.1f}",
              f"{r['instrumental_mag']:.4f}",
              f"{r['vmag']:.3f}" if not pd.isna(r.get("vmag", float("nan"))) else "target"]
             for _, r in df.iterrows()],
        )

        # ── 7. Ensemble linear fit ────────────────────────────────────────────
        log.section("Step 7 – Ensemble photometry (linear regression)")
        ensemble = df.query(
            "auid != 'target' and vmag >= @comp_mag_min and vmag <= @comp_mag_max"
        ).dropna(subset=["vmag", "instrumental_mag"])

        if len(ensemble) < 2:
            raise ValueError(
                f"Only {len(ensemble)} comparison star(s) in range "
                f"{comp_mag_min}–{comp_mag_max}.  Broaden the range."
            )

        x_fit  = ensemble["instrumental_mag"].values
        y_fit  = ensemble["vmag"].values
        coeffs, *_ = np.polyfit(x_fit, y_fit, 1, full=True)
        fit_fn = np.poly1d(coeffs)

        y_pred  = fit_fn(x_fit)
        rms_err = float(np.sqrt(np.mean((y_fit - y_pred) ** 2)))

        log.line(f"Stars used    : {len(ensemble)}  (range {comp_mag_min:.1f}–{comp_mag_max:.1f})")
        log.detail(f"Fit slope     : {coeffs[0]:.6f}")
        log.detail(f"Fit intercept : {coeffs[1]:.6f}")
        log.detail(f"RMS residual  : {rms_err:.4f} mag")
        log.table(
            ["AUID", "inst_mag", "vmag_cat", "vmag_fit", "residual"],
            [[r["auid"],
              f"{r['instrumental_mag']:.4f}",
              f"{r['vmag']:.3f}",
              f"{fit_fn(r['instrumental_mag']):.3f}",
              f"{r['vmag'] - fit_fn(r['instrumental_mag']):+.4f}"]
             for _, r in ensemble.iterrows()],
        )

        # ── 8. Target magnitude ───────────────────────────────────────────────
        log.section("Step 8 – Target magnitude")
        target_inst = float(df[df["auid"] == "target"]["instrumental_mag"].iloc[0])
        target_mag  = float(fit_fn(target_inst))
        log.line(f"Instrumental mag : {target_inst:.4f}")
        log.line(f"Calibrated mag   : {target_mag:.3f}  ± {rms_err:.3f}")

        # ── 9. Check star ─────────────────────────────────────────────────────
        log.section("Step 9 – Check star")
        comp_row  = ensemble.iloc[0]
        check_row = ensemble.iloc[-1] if len(ensemble) >= 2 else comp_row
        check_inst         = float(check_row["instrumental_mag"])
        check_mag_measured = float(fit_fn(check_inst))
        log.detail(f"Check star         : {check_row['auid']}")
        log.detail(f"Catalog magnitude  : {check_row['vmag']:.3f}")
        log.detail(f"Measured magnitude : {check_mag_measured:.3f}")
        log.detail(f"Residual           : {check_row['vmag'] - check_mag_measured:+.4f}")

        # ── 10. Build result + final summary ──────────────────────────────────
        result = PhotometryResult(
            star_name          = star_name,
            fits_path          = fits_path,
            filter_band        = filter_band,
            obs_date           = obs_date,
            jd                 = jd,
            target_mag         = target_mag,
            mag_error          = rms_err,
            chart_id           = chart_id,
            comp_star          = str(comp_row["auid"]),
            comp_mag           = float(comp_row["vmag"]),
            check_star         = str(check_row["auid"]),
            check_mag_measured = check_mag_measured,
            check_mag_catalog  = float(check_row["vmag"]),
            airmass            = airmass,
            ensemble_stars     = list(ensemble["auid"].values),
            notes              = f"Ensemble of {len(ensemble)} stars; RMS={rms_err:.4f}",
            observer_code      = observer_code,
        )

        log.section("Step 10 – Summary")
        log.line(f"{'Star':<22} {star_name}")
        log.line(f"{'Date (UT)':<22} {obs_date.strftime('%Y-%m-%d %H:%M:%S')}")
        log.line(f"{'Julian Date':<22} {jd:.5f}")
        log.line(f"{'Filter':<22} {filter_band}")
        log.line(f"{'Magnitude':<22} {target_mag:.3f}  ± {rms_err:.3f}")
        log.line(f"{'Chart ID':<22} {chart_id}")
        log.line(f"{'Ensemble size':<22} {len(ensemble)} stars")
        log.line(f"{'Check star':<22} {check_row['auid']}  "
                 f"cat={check_row['vmag']:.3f}  meas={check_mag_measured:.3f}")
        if log.path:
            log.line(f"\nLog file: {log.path}")

        return result


# ── AAVSO WebObs Extended-format report ───────────────────────────────────────

def format_aavso_report(result: PhotometryResult) -> str:
    """Format *result* as an AAVSO WebObs Extended submission file."""
    star_no_space = result.star_name.replace(" ", "")
    amass = f"{result.airmass:.3f}" if result.airmass else "na"

    lines = [
        "#TYPE=Extended",
        f"#OBSCODE={result.observer_code}",
        "#SOFTWARE=VSTarget",
        "#DELIM=,",
        "#DATE=JD",
        "#OBSTYPE=CCD",
        "#",
        "#NAME,DATE,MAG,MERR,FILT,TRANS,MTYPE,CNAME,CMF,KNAME,KMF,AMASS,GROUP,CHART,NOTES",
        (
            f"{star_no_space},"
            f"{result.jd:.5f},"
            f"{result.target_mag:.3f},"
            f"{result.mag_error:.3f},"
            f"{result.filter_band},"
            f"NO,"
            f"STD,"
            f"ENSEMBLE,"
            f"na,"
            f"{result.check_star},"
            f"{result.check_mag_catalog:.3f},"
            f"{amass},"
            f"0,"
            f"{result.chart_id},"
            f"{result.notes}"
        ),
    ]
    return "\n".join(lines) + "\n"


def format_summary(result: PhotometryResult) -> str:
    """Human-readable summary of the photometry result."""
    return (
        f"Star:            {result.star_name}\n"
        f"Date (UT):       {result.obs_date.strftime('%Y-%m-%d  %H:%M:%S')}\n"
        f"Julian Date:     {result.jd:.5f}\n"
        f"Filter:          {result.filter_band}\n"
        f"Magnitude:       {result.target_mag:.3f} ± {result.mag_error:.3f}\n"
        f"Chart ID:        {result.chart_id}\n"
        f"Ensemble stars:  {', '.join(result.ensemble_stars)}\n"
        f"Check star:      {result.check_star}  "
        f"(catalog {result.check_mag_catalog:.3f}, "
        f"measured {result.check_mag_measured:.3f})\n"
        f"Airmass:         {result.airmass:.3f}\n"
        f"Image:           {os.path.basename(result.fits_path)}\n"
    )


# ── Background thread ─────────────────────────────────────────────────────────

class PhotometryThread(QThread):
    """Run :func:`run_photometry` in a background thread.

    Signals
    -------
    status(str)                 – Progress messages.
    finished(PhotometryResult)  – Result on success.
    error(str)                  – Error message on failure.
    """

    status   = Signal(str)
    finished = Signal(object)   # PhotometryResult
    error    = Signal(str)

    def __init__(
        self,
        fits_path:       str,
        star_name:       Optional[str]  = None,
        filter_band:     Optional[str]  = None,
        comp_mag_min:    float = 11.0,
        comp_mag_max:    float = 13.5,
        aperture_radius: float = 6.0,
        snr_threshold:   float = 5.0,
        observer_code:   str   = "UNKNOWN",
        log_dir:         Optional[str] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._kwargs = dict(
            fits_path       = fits_path,
            star_name       = star_name,
            filter_band     = filter_band,
            comp_mag_min    = comp_mag_min,
            comp_mag_max    = comp_mag_max,
            aperture_radius = aperture_radius,
            snr_threshold   = snr_threshold,
            observer_code   = observer_code,
            log_dir         = log_dir,
            progress        = self.status.emit,
        )

    def run(self) -> None:
        try:
            result = run_photometry(**self._kwargs)
            self.finished.emit(result)
        except Exception as exc:
            logger.error("Photometry failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))
