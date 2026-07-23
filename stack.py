"""Photometric FITS image stacking with star registration.

Public API
----------
create_photometric_stack(file_paths, output_path, progress_callback, reference_path)
    Standalone function – call directly or via StackThread.

StackThread(QThread)
    Background thread wrapper; emits log/progress/finished/error signals.
"""
from __future__ import annotations

import datetime
import logging
import os
from collections.abc import Callable

import numpy as np
from astropy.io import fits
from PySide6.QtCore import QThread, Signal

try:
    import astroalign as aa  # type: ignore
    _ASTROALIGN_OK = True
except ImportError:
    aa = None  # type: ignore
    _ASTROALIGN_OK = False

logger = logging.getLogger(__name__)


# ── FITS helper ───────────────────────────────────────────────────────────────

def fits_image_data(hdul) -> tuple[np.ndarray, object]:
    """Return ``(data_array, header)`` from the first image-bearing HDU."""
    for hdu in hdul:
        if (
            hdu.data is not None
            and hasattr(hdu.data, "ndim")
            and hdu.data.ndim >= 2
        ):
            return hdu.data, hdu.header
    return hdul[0].data, hdul[0].header


# ── Header provenance helper ──────────────────────────────────────────────────

_SOLVE_MARKERS = ("pinpoint", "astap", "plate solv", "solved", "solving")


def strip_solve_provenance(header: fits.Header) -> None:
    """Remove plate-solve provenance cards inherited from an input frame.

    The stacked image copies its header (including WCS) from an input frame,
    but the stack itself has never been plate-solved — cards such as
    ``PLTSOLVD`` and PinPoint/ASTAP HISTORY/COMMENT lines would falsely claim
    a solve.  The WCS cards themselves are kept: frames are registered to the
    reference frame, so its solution still describes the stacked geometry.
    """
    if "PLTSOLVD" in header:
        del header["PLTSOLVD"]
    for i in range(len(header) - 1, -1, -1):
        card = header.cards[i]
        if card.keyword in ("HISTORY", "COMMENT") and any(
            marker in str(card.value).lower() for marker in _SOLVE_MARKERS
        ):
            del header[i]


# ── Core stacking function ────────────────────────────────────────────────────

def create_photometric_stack(
    file_paths: list[str],
    output_path: str,
    progress_callback: Callable[[int, int, str], None] | None = None,
    reference_path: str | None = None,
) -> bool:
    """Create a photometry-safe light stack.

    Uses star registration (astroalign) and a NaN-aware mean combine.
    No sigma clipping / outlier rejection is performed so the result remains
    linear for differential photometry.  Output is float32 FITS.

    Parameters
    ----------
    file_paths:
        Ordered list of input FITS paths (≥ 2 required).
    output_path:
        Destination FITS path (overwritten if it already exists).
    progress_callback:
        Optional ``(current, total, message)`` callable for UI updates.
    reference_path:
        Frame used as the alignment reference.  Defaults to ``file_paths[0]``.

    Returns
    -------
    bool
        ``True`` on success, ``False`` on recoverable failure.  A
        ``RuntimeError`` is raised when a hard dependency (astroalign) is
        missing.
    """
    try:
        if not file_paths:
            logger.error("No input files provided")
            return False

        if not _ASTROALIGN_OK:
            raise RuntimeError(
                "Photometric stacking requires astroalign for star registration. "
                "Install it with:  pip install astroalign"
            )

        logger.info(
            "Creating photometric light stack from %d files (registered mean, no clipping)",
            len(file_paths),
        )

        if progress_callback:
            progress_callback(0, 100, f"Loading {len(file_paths)} light frames…")

        with fits.open(file_paths[0]) as hdul:
            _d0, header = fits_image_data(hdul)
            header = header.copy()
            data_shape = _d0.shape

        # Reference frame
        candidate = (
            reference_path
            if (reference_path and os.path.exists(reference_path))
            else file_paths[0]
        )
        ref_path = candidate
        with fits.open(candidate) as hdul:
            _ref_d, _ref_h = fits_image_data(hdul)
            ref_full = _ref_d.astype(np.float32)
            ref_header = _ref_h.copy()

        # ── Registration helpers ───────────────────────────────────────────────

        def _is_maxiter_error(exc: Exception) -> bool:
            max_iter_cls = getattr(aa, "MaxIterError", None)
            if max_iter_cls is not None:
                try:
                    return isinstance(exc, max_iter_cls)
                except Exception:
                    pass
            return exc.__class__.__name__ == "MaxIterError"

        def _register(data: np.ndarray, file_path: str) -> np.ndarray:
            if os.path.abspath(file_path) == os.path.abspath(ref_path):
                return data.astype(np.float32, copy=False)
            try:
                aligned, footprint = aa.register(
                    data.astype(np.float32, copy=False),
                    ref_full,
                    fill_value=np.nan,
                )
                aligned = aligned.astype(np.float32, copy=False)
                if footprint is not None:
                    aligned = aligned.copy()
                    aligned[footprint] = np.nan
                return aligned
            except Exception as e:
                # Retry with elevated SEP sub-object limit on deblending overflow
                if any(
                    kw in str(e)
                    for kw in ("deblending overflow", "sub-objects reached",
                               "object deblending overflow")
                ):
                    try:
                        import sep as _sep  # type: ignore
                        _orig = _sep.get_sub_object_limit()
                        _sep.set_sub_object_limit(_orig * 4)
                        try:
                            aligned, footprint = aa.register(
                                data.astype(np.float32, copy=False),
                                ref_full,
                                fill_value=np.nan,
                            )
                            aligned = aligned.astype(np.float32, copy=False)
                            if footprint is not None:
                                aligned = aligned.copy()
                                aligned[footprint] = np.nan
                            return aligned
                        finally:
                            _sep.set_sub_object_limit(_orig)
                    except (ImportError, Exception):
                        pass

                # WCS reprojection fallback
                should_fallback = (
                    _is_maxiter_error(e)
                    or isinstance(e, TypeError)
                    or "Input type for target not supported" in str(e)
                    or "deblending overflow" in str(e)
                    or "sub-objects reached" in str(e)
                )
                if should_fallback:
                    reproj = _try_wcs_reproject(data, file_path)
                    if reproj is not None:
                        logger.warning(
                            "Star registration failed for %s (%s) — used WCS fallback.",
                            os.path.basename(file_path), e,
                        )
                        return reproj
                    logger.warning(
                        "Star registration failed for %s (%s) — stacking without alignment.",
                        os.path.basename(file_path), e,
                    )
                    return data.astype(np.float32, copy=False)
                raise

        def _try_wcs_reproject(data: np.ndarray, file_path: str) -> np.ndarray | None:
            if data.ndim != 2 or ref_full.ndim != 2:
                return None
            try:
                from astropy.wcs import WCS, FITSFixedWarning
                from reproject import reproject_interp  # type: ignore
                import warnings
                warnings.filterwarnings("ignore", category=FITSFixedWarning)
            except ImportError:
                return None
            try:
                with fits.open(file_path) as hdul:
                    _, src_header = fits_image_data(hdul)
                src_wcs = WCS(src_header)
                dst_wcs = WCS(ref_header)
                if not (
                    getattr(src_wcs, "has_celestial", False)
                    and getattr(dst_wcs, "has_celestial", False)
                ):
                    return None
                reproj, footprint = reproject_interp(
                    (data.astype(np.float32, copy=False), src_wcs),
                    dst_wcs,
                    shape_out=ref_full.shape,
                    order="bilinear",
                )
                aligned = np.asarray(reproj, dtype=np.float32)
                if footprint is not None:
                    aligned = aligned.copy()
                    aligned[np.asarray(footprint) <= 0] = np.nan
                return aligned
            except Exception:
                return None

        # ── Validate frames ───────────────────────────────────────────────────

        valid_files: list[str] = []
        for i, file_path in enumerate(file_paths):
            try:
                with fits.open(file_path) as hdul:
                    _fd, _ = fits_image_data(hdul)
                    if _fd is None or _fd.shape != data_shape:
                        logger.warning(
                            "Skipping frame with mismatched dimensions: %s", file_path
                        )
                        continue
                valid_files.append(file_path)
            except Exception as exc:
                logger.warning("Skipping corrupted file %s: %s", file_path, exc)
            if progress_callback and (i + 1) % 10 == 0:
                progress_callback(5, 100, f"Validated {i + 1}/{len(file_paths)} frames…")

        if len(valid_files) < 2:
            logger.error("Not enough valid frames to stack (%d)", len(valid_files))
            return False

        # ── Accumulate ────────────────────────────────────────────────────────

        accumulator = np.zeros(data_shape, dtype=np.float64)
        count = np.zeros(data_shape, dtype=np.uint16)

        for i, file_path in enumerate(valid_files):
            with fits.open(file_path) as hdul:
                _fd, _ = fits_image_data(hdul)
                data = _fd.astype(np.float32)
            data = _register(data, file_path)
            mask = np.isfinite(data)
            accumulator += np.where(mask, data, 0.0).astype(np.float64, copy=False)
            count += mask.astype(np.uint16)
            if progress_callback and (i + 1) % 2 == 0:
                pct = 10 + int(((i + 1) / len(valid_files)) * 80)
                progress_callback(pct, 100, f"Stacking {i + 1}/{len(valid_files)} frames…")

        # ── Mean combine ──────────────────────────────────────────────────────

        mean = np.full(data_shape, np.nan, dtype=np.float32)
        valid = count > 0
        mean[valid] = (accumulator[valid] / count[valid]).astype(np.float32)

        # ── Write output ──────────────────────────────────────────────────────

        strip_solve_provenance(header)
        header["HISTORY"] = f"Photometric stack from {len(valid_files)} frames"
        if "CTYPE1" in header:
            header["HISTORY"] = (
                f"WCS inherited from {os.path.basename(ref_path)}; "
                "stack not independently plate-solved"
            )
        header["NFILES"]  = len(valid_files)
        header["CREATOR"] = "VSTarget Photometric Stacking"
        header["METHOD"]  = "Registered mean (no clipping)"
        header["REFPATH"] = os.path.basename(ref_path)
        header["DATE"]    = datetime.datetime.now().isoformat()

        if progress_callback:
            progress_callback(95, 100, "Saving stack…")

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        hdu = fits.PrimaryHDU(data=mean.astype(np.float32, copy=False), header=header)
        hdu.writeto(output_path, overwrite=True)

        if progress_callback:
            progress_callback(100, 100, "Stack complete")

        logger.info("Photometric stack written: %s", output_path)
        return True

    except RuntimeError:
        raise
    except Exception as exc:
        logger.error("Error creating photometric stack: %s", exc, exc_info=True)
        return False


# ── Background thread ─────────────────────────────────────────────────────────

class StackThread(QThread):
    """Run :func:`create_photometric_stack` in a background thread.

    Signals
    -------
    log(str)               – Human-readable status line.
    progress(int, int, str)– (current, total, message) for progress bars.
    finished(str)          – Output path on success.
    error(str)             – Error message on failure.
    """

    log      = Signal(str)
    progress = Signal(int, int, str)
    finished = Signal(str)
    error    = Signal(str)

    def __init__(
        self,
        file_paths: list[str],
        output_path: str,
        reference_path: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._file_paths    = file_paths
        self._output_path   = output_path
        self._reference_path = reference_path

    def run(self) -> None:
        def _cb(current: int, total: int, msg: str) -> None:
            self.progress.emit(current, total, msg)
            self.log.emit(msg)

        try:
            ok = create_photometric_stack(
                file_paths=self._file_paths,
                output_path=self._output_path,
                progress_callback=_cb,
                reference_path=self._reference_path,
            )
            if ok:
                self.finished.emit(self._output_path)
            else:
                self.error.emit(
                    "Stacking failed — check that all selected images have the "
                    "same dimensions and at least 2 valid frames are present."
                )
        except RuntimeError as exc:
            self.error.emit(str(exc))
        except Exception as exc:
            self.error.emit(f"Unexpected error during stacking: {exc}")
