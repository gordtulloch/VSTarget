"""ASTAP plate-solver integration.

Runs the ASTAP command-line solver on a FITS file, captures its output line
by line, and verifies that WCS keywords were written to the header.

ASTAP documentation: https://www.hnsky.org/astap.htm
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import List, Optional, Tuple

from astropy.io import fits
from astropy.wcs import WCS
from PySide6.QtCore import QThread, Signal

# ── Locate ASTAP ──────────────────────────────────────────────────────────────

_WINDOWS_CANDIDATES = [
    r"C:\Program Files\astap\astap.exe",
    r"C:\Program Files (x86)\astap\astap.exe",
    r"C:\astap\astap.exe",
    r"C:\tools\astap\astap.exe",
]

_UNIX_CANDIDATES = [
    "/usr/bin/astap",
    "/usr/local/bin/astap",
    "/opt/astap/astap",
    "/opt/homebrew/bin/astap",   # macOS Homebrew
]


def find_astap() -> Optional[str]:
    """Return the path to the ASTAP executable, or ``None`` if not found.

    Search order:
    1. ``astap`` / ``astap.exe`` on the system ``PATH``
    2. Common installation locations for Windows / Linux / macOS
    """
    found = shutil.which("astap") or shutil.which("ASTAP")
    if found:
        return found

    candidates = _WINDOWS_CANDIDATES if os.name == "nt" else _UNIX_CANDIDATES
    for path in candidates:
        if os.path.isfile(path):
            return path

    return None


# ── Core plate-solve function ─────────────────────────────────────────────────

def platesolve_fits(
    fits_path: str,
    astap_path: str,
    ra_hint:        Optional[float] = None,   # decimal degrees
    dec_hint:       Optional[float] = None,   # decimal degrees
    fov_hint:       Optional[float] = None,   # degrees
    search_radius:  float = 90.0,             # degrees
    downsample:     int   = 0,                # 0 = auto
    progress_cb:    Optional[callable] = None,
) -> Tuple[bool, str]:
    """Run ASTAP to plate-solve *fits_path* and update its WCS header in-place.

    Parameters
    ----------
    fits_path:      FITS file to solve (updated in-place on success).
    astap_path:     Full path to the ``astap`` / ``astap.exe`` binary.
    ra_hint:        Approximate RA in decimal degrees (optional; speeds up solve).
    dec_hint:       Approximate Dec in decimal degrees (optional).
    fov_hint:       Approximate field-of-view width in degrees (optional).
    search_radius:  ASTAP search radius in degrees (default 90 = wide search).
    downsample:     ASTAP downsampling factor (0 = auto).
    progress_cb:    Optional ``(line: str)`` callback for live output.

    Returns
    -------
    (success, message)
        *success* is ``True`` when ASTAP solved and wrote WCS to the header.
    """
    if not os.path.isfile(astap_path):
        return False, f"ASTAP executable not found: {astap_path}"
    if not os.path.isfile(fits_path):
        return False, f"FITS file not found: {fits_path}"

    # ── Build command ─────────────────────────────────────────────────────────
    cmd: List[str] = [astap_path, "-f", fits_path, "-update"]

    # RA hint: ASTAP expects hours (0-24), not degrees
    if ra_hint is not None:
        cmd += ["-ra", f"{ra_hint / 15.0:.6f}"]

    # SPD (South Pole Distance) = 90 + Dec
    if dec_hint is not None:
        spd = 90.0 + dec_hint
        cmd += ["-spd", f"{spd:.4f}"]

    if fov_hint is not None:
        cmd += ["-fov", f"{fov_hint:.4f}"]

    # Use a tighter search if we have a position hint
    effective_radius = min(search_radius, 30.0) if ra_hint is not None else search_radius
    cmd += ["-r", f"{effective_radius:.1f}"]

    if downsample > 0:
        cmd += ["-z", str(downsample)]

    if progress_cb:
        progress_cb(f"Running: {' '.join(cmd)}")

    # ── Execute ASTAP ─────────────────────────────────────────────────────────
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        return False, f"Failed to launch ASTAP: {exc}"

    output_lines: List[str] = []
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.rstrip()
            output_lines.append(line)
            if progress_cb and line:
                progress_cb(line)
        proc.wait(timeout=300)
    except subprocess.TimeoutExpired:
        proc.kill()
        return False, "ASTAP timed out after 5 minutes."
    except Exception as exc:
        proc.kill()
        return False, f"ASTAP error: {exc}"

    success = proc.returncode == 0

    # Parse ASTAP output for solution details
    solution_line = next(
        (ln for ln in output_lines if "solution found" in ln.lower()), ""
    )
    failed_line = next(
        (ln for ln in output_lines if "solving failed" in ln.lower()), ""
    )

    if success:
        # Verify WCS was actually written to the FITS header
        try:
            with fits.open(fits_path) as hdul:
                wcs = WCS(hdul[0].header)
            if not wcs.has_celestial:
                return False, (
                    "ASTAP reported success but no celestial WCS was found in the header."
                )
        except Exception as exc:
            return False, f"ASTAP succeeded but WCS verification failed: {exc}"

        msg = solution_line or "Plate solve successful — WCS written to FITS header."
        return True, msg
    else:
        msg = failed_line or (
            "ASTAP could not solve this field.  "
            "Try providing RA/Dec hints or increasing the search radius."
        )
        return False, msg


def ra_dec_from_header(fits_path: str) -> Tuple[Optional[float], Optional[float]]:
    """Extract approximate RA/Dec hints from common FITS header keywords.

    Returns ``(ra_deg, dec_deg)`` or ``(None, None)`` if not found.
    Useful for seeding ASTAP when the image has been tagged by the telescope.
    """
    try:
        with fits.open(fits_path) as hdul:
            h = hdul[0].header
            # Try object-coordinate keywords first (decimal degrees)
            for ra_kw in ("OBJCTRA", "RA", "CRVAL1", "RA_OBJ"):
                val = h.get(ra_kw)
                if val is not None:
                    try:
                        ra = float(val)
                        # OBJCTRA is sometimes stored as HH MM SS string
                        if isinstance(val, str) and ":" in val:
                            from astropy.coordinates import Angle
                            import astropy.units as u
                            ra = float(Angle(val, unit=u.hour).deg)
                        for dec_kw in ("OBJCTDEC", "DEC", "CRVAL2", "DEC_OBJ"):
                            dec_val = h.get(dec_kw)
                            if dec_val is not None:
                                try:
                                    dec = float(dec_val)
                                    if isinstance(dec_val, str) and ":" in dec_val:
                                        from astropy.coordinates import Angle
                                        import astropy.units as u
                                        dec = float(Angle(dec_val, unit=u.deg).deg)
                                    return ra, dec
                                except (ValueError, TypeError):
                                    continue
                    except (ValueError, TypeError):
                        continue
    except Exception:
        pass
    return None, None


# ── Background thread ─────────────────────────────────────────────────────────

class PlateSolveThread(QThread):
    """Run :func:`platesolve_fits` in a background thread.

    Signals
    -------
    log(str)              – Live ASTAP output line.
    finished(bool, str)   – (success, message) on completion.
    """

    log      = Signal(str)
    finished = Signal(bool, str)

    def __init__(
        self,
        fits_path:      str,
        astap_path:     str,
        ra_hint:        Optional[float] = None,
        dec_hint:       Optional[float] = None,
        fov_hint:       Optional[float] = None,
        search_radius:  float = 90.0,
        downsample:     int   = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._fits_path    = fits_path
        self._astap_path   = astap_path
        self._ra_hint      = ra_hint
        self._dec_hint     = dec_hint
        self._fov_hint     = fov_hint
        self._search_radius = search_radius
        self._downsample   = downsample

    def run(self) -> None:
        success, message = platesolve_fits(
            fits_path     = self._fits_path,
            astap_path    = self._astap_path,
            ra_hint       = self._ra_hint,
            dec_hint      = self._dec_hint,
            fov_hint      = self._fov_hint,
            search_radius = self._search_radius,
            downsample    = self._downsample,
            progress_cb   = self.log.emit,
        )
        self.finished.emit(success, message)
