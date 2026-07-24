"""Photometric transformation-coefficient generator.

Given plate-solved FITS images of an AAVSO standard field
(https://apps.aavso.org/vsd/stdfields), one image per filter, measures
instrumental magnitudes for every standard-field reference star and fits
the Henden two-color transformation coefficients (Tbv, Tv_bv, Tvr, ... —
see Henden, "Astronomical Photometry", and Bruce Gary's CCD transformation
equations for differential photometry).

Workflow:
  1. Resolve the standard field's RA/Dec (fixed for the six named fields;
     read from the first image's WCS field centre for a Landolt field).
  2. Download the field's reference-star catalog magnitudes from AAVSO VSP.
  3. For each supplied filter image, reuse photometry.py's source
     extraction + WCS matching + aperture photometry to get raw
     instrumental magnitudes for every matched reference star.
  4. Fit each computable transform (least squares) from the merged
     catalog + instrumental magnitudes.
"""
from __future__ import annotations

import contextlib
import logging
import math
from dataclasses import dataclass, field

import astropy.units as u
import numpy as np
import requests
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS
from PySide6.QtCore import QThread, Signal

from photometry import extract_sources, match_and_measure

logger = logging.getLogger(__name__)


# ── Standard fields ─────────────────────────────────────────────────────────

# Fixed AAVSO/Henden standard fields: name -> (RA, Dec) in decimal degrees.
STANDARD_FIELDS: dict[str, tuple[float, float]] = {
    "M67":         (132.825, 11.8),
    "NGC 7790":    (359.6, 61.217),
    "M11":         (282.775, -6.267),
    "NGC 1252":    (47.704, -57.767),
    "NGC 3532":    (166.412, -58.752),
    "Melotte 111": (186.275, 26.1),
}

# Not in STANDARD_FIELDS: its RA/Dec comes from the plate-solved image's
# WCS field centre instead of a fixed coordinate.
LANDOLT_FIELD = "Landolt Field"

STANDARD_FIELD_NAMES = [*STANDARD_FIELDS, LANDOLT_FIELD]

_BANDS = ("U", "B", "V", "R", "I")

_VSP_STDFIELD_URL = (
    "https://app.aavso.org/vsp/api/chart/"
    "?format=json&special=std_field&fov=179&maglimit=16.5&ra={ra}&dec={dec}"
)


def fetch_standard_field_stars(ra_deg: float, dec_deg: float) -> list[dict]:
    """Download standard-field reference stars and their catalog magnitudes.

    Returns a list of dicts, one per reference star, each with ``auid``,
    ``ra``, ``dec`` (sexagesimal, as returned by VSP) plus one uppercase
    key per band in :data:`_BANDS` that VSP reported a magnitude for.
    """
    url = _VSP_STDFIELD_URL.format(ra=ra_deg, dec=dec_deg)
    logger.info("VSP standard-field request: %s", url)
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    stars: list[dict] = []
    for entry in data.get("photometry", []):
        star: dict = {
            "auid": entry["auid"],
            "ra":   entry["ra"],
            "dec":  entry["dec"],
        }
        for band in entry.get("bands", []):
            name = band.get("band")
            if name in _BANDS:
                with contextlib.suppress(TypeError, ValueError):
                    star[name] = float(band["mag"])
        stars.append(star)
    logger.info("Downloaded %d standard-field reference stars", len(stars))
    return stars


def _filter_stars_by_radius(
    stars: list[dict],
    ra_deg: float,
    dec_deg: float,
    radius_arcmin: float,
) -> list[dict]:
    """Keep only *stars* within *radius_arcmin* of (ra_deg, dec_deg).

    VSP's std-field chart request always queries a ~3 deg-diameter area, far
    wider than the actual calibrated standard field (typically tens of
    arcmin). For wide-field images, the WCS solution's positional accuracy
    degrades away from the field centre, so matching against the full
    catalog radius picks up stars whose WCS-projected pixel position drifts
    past the fixed match tolerance. Restricting to the field's actual size
    keeps matching close to the (most accurate) field centre.
    """
    if not stars:
        return stars
    center = SkyCoord(ra_deg, dec_deg, unit=u.deg)
    kept = []
    for star in stars:
        try:
            sky = SkyCoord(star["ra"], star["dec"], unit=(u.hourangle, u.deg))
        except Exception:
            continue
        if center.separation(sky).arcmin <= radius_arcmin:
            kept.append(star)
    return kept


# ── Transform definitions ───────────────────────────────────────────────────

# (name, x token, y token, reciprocal).  Each token is "A-b": an uppercase
# letter reads a star's catalog magnitude, a lowercase letter its measured
# instrumental magnitude in that filter.  "reciprocal" transforms are fit
# as x vs. y and then reported as 1/slope (Henden's Tub, Tbv, ... convention).
TRANSFORM_DEFS: list[tuple[str, str, str, bool]] = [
    ("Tub",    "U-B", "u-b", True),
    ("Tu_ub",  "U-B", "U-u", False),
    ("Tb_ub",  "U-B", "B-b", False),
    ("Tbv",    "B-V", "b-v", True),
    ("Tb_bv",  "B-V", "B-b", False),
    ("Tv_bv",  "B-V", "V-v", False),
    ("Tbr",    "B-R", "b-r", True),
    ("Tb_br",  "B-R", "B-b", False),
    ("Tbi",    "B-I", "b-i", True),
    ("Tb_bi",  "B-I", "B-b", False),
    ("Tvr",    "V-R", "v-r", True),
    ("Tv_vr",  "V-R", "V-v", False),
    ("Tr_vr",  "V-R", "R-r", False),
    ("Tri",    "R-I", "r-i", True),
    ("Tr_ri",  "R-I", "R-r", False),
    ("Ti_ri",  "R-I", "I-i", False),
    ("Tvi",    "V-I", "v-i", True),
    ("Tv_vi",  "V-I", "V-v", False),
    ("Ti_vi",  "V-I", "I-i", False),
    ("Tr_vi",  "V-I", "R-r", False),
]

_RECIPROCAL_BY_NAME = {name: recip for name, _, _, recip in TRANSFORM_DEFS}
_TOKENS_BY_NAME      = {name: (x, y) for name, x, y, _ in TRANSFORM_DEFS}


def _needed_filters(x_token: str, y_token: str) -> set[str]:
    letters = (x_token + y_token).replace("-", "")
    return {c.lower() for c in letters}


def available_transforms(available_filters: set[str]) -> list[str]:
    """Return transform names computable given the (lowercase) filters measured."""
    return [
        name for name, x_tok, y_tok, _ in TRANSFORM_DEFS
        if _needed_filters(x_tok, y_tok) <= available_filters
    ]


def _diff_value(token: str, star: dict) -> float | None:
    """Evaluate a 'A-b' token against one star's catalog + instrumental magnitudes."""
    left, right = token.split("-")
    lv = star.get(left)
    rv = star.get(right)
    if lv is None or rv is None:
        return None
    return lv - rv


# ── Linear regression ───────────────────────────────────────────────────────

def _linregress(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float, float]:
    """Least-squares fit.  Returns (slope, intercept, r_squared, slope_stderr)."""
    slope, intercept = np.polyfit(x, y, 1)
    y_pred = slope * x + intercept
    residuals = y - y_pred
    ss_res = float(np.sum(residuals ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    n = len(x)
    if n > 2:
        x_var = float(np.sum((x - x.mean()) ** 2))
        slope_stderr = math.sqrt((ss_res / (n - 2)) / x_var) if x_var > 0 else 0.0
    else:
        slope_stderr = 0.0
    return float(slope), float(intercept), r_squared, slope_stderr


@dataclass
class TransformPoint:
    auid: str
    x: float
    y: float
    used: bool = True


@dataclass
class TransformResult:
    name: str
    x_label: str
    y_label: str
    value: float
    error: float
    r_squared: float
    points: list[TransformPoint] = field(default_factory=list)

    def recompute(self) -> None:
        """Refit using only points with ``used=True``; updates value/error/r_squared."""
        used = [p for p in self.points if p.used]
        if len(used) < 2:
            self.value, self.error, self.r_squared = 0.0, 0.0, 0.0
            return
        x = np.array([p.x for p in used])
        y = np.array([p.y for p in used])
        slope, _intercept, r2, slope_err = _linregress(x, y)
        if _RECIPROCAL_BY_NAME[self.name] and slope != 0:
            self.value = 1.0 / slope
            self.error = slope_err / slope ** 2
        else:
            self.value = slope
            self.error = slope_err
        self.r_squared = r2


def compute_transforms(
    stars: list[dict],
    available_filters: set[str],
) -> dict[str, TransformResult]:
    """Compute every transform that *available_filters* makes possible.

    *stars* is a list of per-star dicts, each merging catalog magnitudes
    (uppercase keys 'U','B','V','R','I') and measured instrumental
    magnitudes (lowercase keys) for whichever filters were measured for
    that star.  Missing keys mean "no data for this star/filter".
    """
    results: dict[str, TransformResult] = {}
    for name in available_transforms(available_filters):
        x_tok, y_tok = _TOKENS_BY_NAME[name]
        points = []
        for star in stars:
            x = _diff_value(x_tok, star)
            y = _diff_value(y_tok, star)
            if x is None or y is None:
                continue
            points.append(TransformPoint(auid=star["auid"], x=x, y=y))
        result = TransformResult(
            name=name, x_label=x_tok, y_label=y_tok,
            value=0.0, error=0.0, r_squared=0.0, points=points,
        )
        result.recompute()
        results[name] = result
    return results


# ── Per-image measurement ───────────────────────────────────────────────────

def _log_match_diagnostics(
    stars: list[dict],
    sources: np.ndarray,
    wcs: WCS,
    img_shape: tuple[int, int],
    match_tol_px: float,
    emit: callable,
) -> None:
    """On a failed/near-failed match, explain *why* via the progress log.

    Projects each star to pixel space (independent of ``match_and_measure``,
    which discards this on a miss) and reports how many land inside the
    image, plus the distance from each in-bounds star to its nearest
    extracted source — the single most useful number for deciding whether
    ``match_tol_px`` needs raising or the WCS solution itself is bad.
    """
    img_h, img_w = img_shape
    if len(sources) == 0:
        emit("  no sources extracted in this image — check SNR threshold")
        return
    src_x = np.asarray([float(s["x"]) for s in sources])
    src_y = np.asarray([float(s["y"]) for s in sources])
    in_bounds = 0
    nearest: list[float] = []
    for star in stars:
        try:
            sky = SkyCoord(star["ra"], star["dec"], unit=(u.hourangle, u.deg))
            px, py = (float(v) for v in SkyCoord.to_pixel(sky, wcs=wcs, origin=1))
        except Exception:
            continue
        if not (0 <= px < img_w and 0 <= py < img_h):
            continue
        in_bounds += 1
        dist = float(np.min(np.hypot(src_x - px, src_y - py)))
        nearest.append(dist)
    emit(
        f"  {len(sources)} sources extracted; {in_bounds}/{len(stars)} catalog "
        f"stars project inside the {img_w}x{img_h} frame"
    )
    if nearest:
        nearest.sort()
        median = nearest[len(nearest) // 2]
        emit(
            f"  nearest-source distance for in-bounds stars: "
            f"min={nearest[0]:.1f}px median={median:.1f}px "
            f"(match tolerance is {match_tol_px:.1f}px)"
        )


def measure_standard_field(
    fits_path: str,
    stars: list[dict],
    snr_threshold: float = 5.0,
    aperture_radius: float = 6.0,
    match_tol_px: float = 5.0,
    progress: callable | None = None,
) -> dict[str, float]:
    """Measure raw instrumental magnitudes for *stars* in one filter image.

    Returns ``{auid: instrumental_mag}`` for stars matched to a detected
    source; *stars* itself (each a dict with ``auid``/``ra``/``dec``) is
    not mutated by the caller's copy of the list — pass fresh dicts per
    call if reusing the same catalog across multiple filter images.
    """
    with fits.open(fits_path) as hdul:
        raw_data = hdul[0].data.astype(np.float32)
        header   = hdul[0].header.copy()
    wcs = WCS(header)
    sources, back_arr = extract_sources(raw_data, snr_threshold)
    data_sub = raw_data - back_arr
    df = match_and_measure(data_sub, sources, stars, wcs, aperture_radius, match_tol_px)
    if df.empty:
        if progress:
            _log_match_diagnostics(stars, sources, wcs, raw_data.shape, match_tol_px, progress)
        return {}
    return dict(zip(df["auid"], df["instrumental_mag"], strict=True))


def _check_field_matches_images(
    std_field_name: str,
    ra_deg: float,
    dec_deg: float,
    filter_images: dict[str, str],
    field_radius_arcmin: float,
) -> None:
    """Fail fast if a supplied image's WCS centre is nowhere near the chosen
    standard field — almost always means the wrong field was picked from the
    dropdown (e.g. left on the default) rather than an image/photometry
    problem, and the resulting "no matches" error otherwise gives no hint.
    """
    center = SkyCoord(ra_deg, dec_deg, unit=u.deg)
    for filt, path in filter_images.items():
        with fits.open(path) as hdul:
            header = hdul[0].header.copy()
            img_h, img_w = hdul[0].data.shape
        wcs = WCS(header)
        img_center = wcs.pixel_to_world(img_w / 2, img_h / 2)
        img_corner = wcs.pixel_to_world(0, 0)
        half_diag_deg = img_center.separation(img_corner).deg
        allowed_deg = half_diag_deg + field_radius_arcmin / 60.0
        sep_deg = center.separation(img_center).deg
        if sep_deg > allowed_deg:
            raise ValueError(
                f"'{filt}' image is {sep_deg:.1f} deg from '{std_field_name}' "
                f"(RA={ra_deg:.3f} Dec={dec_deg:.3f}) — its field centre is "
                f"RA={img_center.ra.deg:.3f} Dec={img_center.dec.deg:.3f}. "
                f"Check that the correct standard field is selected."
            )


# ── Full run ─────────────────────────────────────────────────────────────────

@dataclass
class TransformRunResult:
    std_field_name:      str
    ra_deg:               float
    dec_deg:              float
    num_reference_stars:  int
    available_filters:    set[str]
    transforms:           dict[str, TransformResult]


def run_transform_generation(
    std_field_name: str,
    filter_images: dict[str, str],
    snr_threshold: float = 5.0,
    aperture_radius: float = 6.0,
    field_radius_arcmin: float = 30.0,
    match_tol_px: float = 5.0,
    progress: callable | None = None,
) -> TransformRunResult:
    """Compute transformation coefficients from standard-field FITS images.

    *filter_images* maps filter letter (any case, e.g. ``"V"``) to the path
    of one plate-solved FITS image taken through that filter.
    """
    def emit(msg: str) -> None:
        if progress:
            progress(msg)

    if std_field_name == LANDOLT_FIELD:
        first_path = next(iter(filter_images.values()))
        emit(f"Reading field centre from {first_path}")
        with fits.open(first_path) as hdul:
            wcs_hdr = hdul[0].header.copy()
            img_h, img_w = hdul[0].data.shape
        wcs = WCS(wcs_hdr)
        center = wcs.pixel_to_world(img_w / 2, img_h / 2)
        ra_deg, dec_deg = float(center.ra.deg), float(center.dec.deg)
    elif std_field_name in STANDARD_FIELDS:
        ra_deg, dec_deg = STANDARD_FIELDS[std_field_name]
    else:
        raise ValueError(f"Unknown standard field '{std_field_name}'")

    emit(f"Downloading standard-field reference stars near RA={ra_deg:.3f} Dec={dec_deg:.3f}")
    catalog_stars = fetch_standard_field_stars(ra_deg, dec_deg)
    if len(catalog_stars) < 3:
        raise ValueError("Fewer than 3 standard-field reference stars returned by VSP")

    if std_field_name in STANDARD_FIELDS:
        _check_field_matches_images(
            std_field_name, ra_deg, dec_deg, filter_images, field_radius_arcmin,
        )

    if field_radius_arcmin > 0:
        before = len(catalog_stars)
        catalog_stars = _filter_stars_by_radius(catalog_stars, ra_deg, dec_deg, field_radius_arcmin)
        emit(
            f"Restricted to {len(catalog_stars)}/{before} reference stars within "
            f"{field_radius_arcmin:.0f}' of field centre"
        )
        if len(catalog_stars) < 3:
            raise ValueError(
                f"Fewer than 3 standard-field reference stars within "
                f"{field_radius_arcmin:.0f}' of field centre — try a larger matching field radius"
            )

    merged: dict[str, dict] = {
        s["auid"]: {"auid": s["auid"], **{b: s[b] for b in _BANDS if b in s}}
        for s in catalog_stars
    }

    available_filters: set[str] = set()
    for filt, path in filter_images.items():
        letter = filt.strip().lower()
        if not letter:
            continue
        emit(f"Measuring {filt} filter image: {path}")
        base_stars = [dict(s) for s in catalog_stars]
        inst_mags = measure_standard_field(
            path, base_stars, snr_threshold, aperture_radius, match_tol_px, emit,
        )
        if not inst_mags:
            emit(f"WARNING: no reference stars matched in {filt} image")
            continue
        available_filters.add(letter)
        for auid, mag in inst_mags.items():
            if auid in merged:
                merged[auid][letter] = mag

    if not available_filters:
        raise ValueError("No standard-field reference stars matched in any supplied image")

    transforms = compute_transforms(list(merged.values()), available_filters)
    if not transforms:
        raise ValueError("No transforms can be computed with the filters supplied")

    emit(f"Computed {len(transforms)} transform(s) from {len(available_filters)} filter(s)")

    return TransformRunResult(
        std_field_name      = std_field_name,
        ra_deg               = ra_deg,
        dec_deg              = dec_deg,
        num_reference_stars  = len(catalog_stars),
        available_filters    = available_filters,
        transforms            = transforms,
    )


class TransformGeneratorThread(QThread):
    """Runs :func:`run_transform_generation` off the UI thread.

    Signals:
        status(str)                 – Progress messages.
        finished(TransformRunResult) – Result on success.
        error(str)                  – Error message on failure.
    """

    status   = Signal(str)
    finished = Signal(object)
    error    = Signal(str)

    def __init__(
        self,
        std_field_name: str,
        filter_images: dict[str, str],
        snr_threshold: float = 5.0,
        aperture_radius: float = 6.0,
        field_radius_arcmin: float = 30.0,
        match_tol_px: float = 5.0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._kwargs = {
            "std_field_name":       std_field_name,
            "filter_images":        filter_images,
            "snr_threshold":        snr_threshold,
            "aperture_radius":      aperture_radius,
            "field_radius_arcmin":  field_radius_arcmin,
            "match_tol_px":         match_tol_px,
            "progress":             self.status.emit,
        }

    def run(self) -> None:
        try:
            result = run_transform_generation(**self._kwargs)
            self.finished.emit(result)
        except Exception as exc:
            logger.error("Transform generation failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))
