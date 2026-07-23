"""Analysis panel: list, inspect, and act on FITS images in the working directory."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QFont

# ── FITS library (optional – graceful fallback if missing) ────────────────────

try:
    from astropy.io import fits as _astropy_fits  # type: ignore
    _ASTROPY_OK = True
except ImportError:
    _ASTROPY_OK = False

FITS_EXTENSIONS = {".fit", ".fits", ".fts"}


# ── FITS header reader ────────────────────────────────────────────────────────

def _read_header(filepath: str) -> dict:
    """Return a dict of display-relevant FITS header values, or {} on failure."""
    if not _ASTROPY_OK:
        return {}
    try:
        with _astropy_fits.open(
            filepath,
            memmap=False,
            do_not_scale_image_data=True,
            ignore_missing_simple=True,
        ) as hdul:
            hdr = hdul[0].header
            exptime_raw = hdr.get("EXPTIME") or hdr.get("EXPOSURE") or 0.0
            # Check WCS validity
            try:
                from astropy.wcs import WCS as _WCS
                import warnings as _w
                with _w.catch_warnings():
                    _w.simplefilter("ignore")
                    wcs = _WCS(hdr)
                has_wcs = bool(wcs.has_celestial)
            except Exception:
                has_wcs = False
            return {
                "has_wcs":   has_wcs,
                "object":    str(hdr.get("OBJECT",   "")).strip(),
                "imagetyp":  str(hdr.get("IMAGETYP", "")).strip(),
                "filter":    str(hdr.get("FILTER",   "")).strip(),
                "exptime":   float(exptime_raw),
                "xbinning":  int(hdr.get("XBINNING", 1)),
                "ybinning":  int(hdr.get("YBINNING", 1)),
                "ut":        str(
                    hdr.get("DATE-OBS") or hdr.get("DATE-END") or hdr.get("UT", "")
                ).strip(),
            }
    except Exception:
        return {}


# ── Background loader thread ──────────────────────────────────────────────────

class _HeaderLoader(QThread):
    """Load FITS headers in a background thread, emitting one signal per file."""

    row_ready = Signal(int, dict)   # (row index, header dict)
    done = Signal(int)              # total rows loaded

    def __init__(self, filepaths: list[str], parent=None) -> None:
        super().__init__(parent)
        self._paths = filepaths
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        for i, path in enumerate(self._paths):
            if self._stop:
                break
            self.row_ready.emit(i, _read_header(path))
        self.done.emit(len(self._paths))


# ── Images panel dialog ───────────────────────────────────────────────────────

_GRAY  = QBrush(QColor("gray"))
_BLACK = QBrush(QColor("black"))
_GREEN = QBrush(QColor("#2e7d32"))   # dark green  – WCS solved
_RED   = QBrush(QColor("#c62828"))   # dark red    – no WCS

# Column indices
_C_CHECK    = 0
_C_FILENAME = 1
_C_WCS      = 2
_C_OBJECT   = 3
_C_TYPE     = 4
_C_FILTER   = 5
_C_EXPTIME  = 6
_C_BINNING  = 7
_C_UT       = 8

_HEADERS = ["\u00a0", "Filename", "WCS", "Object", "Type", "Filter", "Exp (s)", "Binning", "UT"]


class ImagesPanel(QDialog):
    """Modeless dialog that lists FITS images from the analysis working directory.

    Columns are populated from the FITS primary header: OBJECT, IMAGETYP,
    FILTER, EXPTIME (formatted ``9999.99``), XBINNINGxYBINNING, and
    DATE-OBS (UT).  Header reading happens in a background thread so the UI
    remains responsive for large directories.

    The action buttons operate on whichever rows are check-marked; only
    Header remains a placeholder.
    """

    def __init__(self, working_dir: str, parent=None) -> None:
        super().__init__(parent)
        self._working_dir = working_dir
        self._loader: _HeaderLoader | None = None
        self._stacker = None    # StackThread – kept alive until done
        self._analyzer = None   # PhotometryThread – kept alive until done
        self._solver = None     # PlateSolveThread – kept alive until done
        self._calibrator = None  # CalibrationThread – kept alive until done

        self.setWindowTitle("Analysis – Images")
        self.resize(1060, 560)
        # Keep maximize button; stay non-modal
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowMinimizeButtonHint
        )
        self.setModal(False)

        self._build_ui()
        self._load_images()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # ── Directory / toolbar row ───────────────────────────────────────────
        top_row = QHBoxLayout()
        self._dir_lbl = QLabel()
        self._dir_lbl.setStyleSheet("font-size: 11px; color: #555;")
        self._dir_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        top_row.addWidget(self._dir_lbl, 1)

        refresh_btn = QPushButton("↺  Refresh")
        refresh_btn.setToolTip("Re-scan the working directory")
        refresh_btn.clicked.connect(self._load_images)

        sel_all_btn = QPushButton("Select All")
        sel_all_btn.clicked.connect(lambda: self._set_all_checked(True))

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(lambda: self._set_all_checked(False))

        for w in (refresh_btn, sel_all_btn, clear_btn):
            top_row.addWidget(w)
        root.addLayout(top_row)

        # ── Image table ───────────────────────────────────────────────────────
        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(_HEADERS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(22)
        self._table.setSortingEnabled(False)   # enabled after load
        self._table.cellDoubleClicked.connect(self._on_cell_double_clicked)

        mono = QFont("Consolas", 9)
        mono.setStyleHint(QFont.Monospace)
        self._table.setFont(mono)

        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)

        col_widths = {
            _C_CHECK:    28,
            _C_FILENAME: 200,
            _C_WCS:       46,
            _C_OBJECT:   120,
            _C_TYPE:      90,
            _C_FILTER:    60,
            _C_EXPTIME:   75,
            _C_BINNING:   60,
        }
        for col, w in col_widths.items():
            self._table.setColumnWidth(col, w)

        # Center-align the checkbox column header
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        root.addWidget(self._table, 1)

        # ── Status / progress row ─────────────────────────────────────────────
        status_row = QHBoxLayout()
        self._status_lbl = QLabel("Ready.")
        status_row.addWidget(self._status_lbl, 1)
        self._progress = QProgressBar()
        self._progress.setFixedWidth(180)
        self._progress.setVisible(False)
        status_row.addWidget(self._progress)
        root.addLayout(status_row)

        # ── Action buttons ────────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        self._stack_btn     = QPushButton("Stack")
        self._delete_btn    = QPushButton("Delete")
        self._header_btn    = QPushButton("Header")
        self._solve_btn     = QPushButton("🔭 Plate Solve")
        self._analyze_btn   = QPushButton("Analyze")
        self._calibrate_btn = QPushButton("📏 Calibrate Exposure")

        self._stack_btn.setToolTip("Stack checked images")
        self._delete_btn.setToolTip("Delete checked images from disk")
        self._header_btn.setToolTip("Show full FITS header for the first checked image")
        self._solve_btn.setToolTip(
            "Plate-solve checked image(s) with ASTAP and write WCS to the FITS header"
        )
        self._analyze_btn.setToolTip("Aperture photometry + AAVSO report")
        self._calibrate_btn.setToolTip(
            "Measure this telescope/filter's throughput from the checked image's\n"
            "comparison stars, for exposure suggestions in the observation plan"
        )

        self._stack_btn.clicked.connect(self._on_stack)
        self._delete_btn.clicked.connect(self._on_delete)
        self._header_btn.clicked.connect(self._on_header)
        self._solve_btn.clicked.connect(self._on_plate_solve)
        self._analyze_btn.clicked.connect(self._on_analyze)
        self._calibrate_btn.clicked.connect(self._on_calibrate)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)

        for w in (self._stack_btn, self._delete_btn,
                  self._header_btn, self._solve_btn, self._analyze_btn,
                  self._calibrate_btn):
            btn_row.addWidget(w)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── Loading ───────────────────────────────────────────────────────────────

    def _load_images(self) -> None:
        """Scan the working directory and populate the table."""
        self._stop_loader()
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)

        if not self._working_dir or not os.path.isdir(self._working_dir):
            self._dir_lbl.setText("No working directory set.")
            self._status_lbl.setText(
                "Use Analysis → Select Working Directory… to choose a folder."
            )
            return

        self._dir_lbl.setText(f"  {self._working_dir}")

        filepaths = sorted(
            str(p)
            for p in Path(self._working_dir).iterdir()
            if p.is_file() and p.suffix.lower() in FITS_EXTENSIONS
        )

        if not filepaths:
            self._status_lbl.setText("No FITS files found (.fit / .fits / .fts).")
            return

        if not _ASTROPY_OK:
            self._status_lbl.setText(
                "⚠  astropy is not installed — run: pip install astropy"
            )

        # Populate skeleton rows; headers filled by background thread
        self._table.setRowCount(len(filepaths))
        for row, path in enumerate(filepaths):
            self._table.setItem(row, _C_CHECK, self._make_checkbox_item())

            name_item = QTableWidgetItem(os.path.basename(path))
            name_item.setData(Qt.UserRole, path)          # store full path
            name_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self._table.setItem(row, _C_FILENAME, name_item)

            for col in range(_C_WCS, len(_HEADERS)):
                ph = QTableWidgetItem("…")
                ph.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                ph.setForeground(_GRAY)
                self._table.setItem(row, col, ph)

        self._progress.setMaximum(len(filepaths))
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._status_lbl.setText(
            f"Reading headers for {len(filepaths)} file(s)…"
        )

        self._loader = _HeaderLoader(filepaths, parent=self)
        self._loader.row_ready.connect(self._on_row_ready)
        self._loader.done.connect(self._on_load_done)
        self._loader.start()

    @staticmethod
    def _make_checkbox_item() -> QTableWidgetItem:
        item = QTableWidgetItem()
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Unchecked)
        return item

    def _on_row_ready(self, row: int, hdr: dict) -> None:
        self._progress.setValue(row + 1)

        def _fill(col: int, text: str) -> None:
            item = self._table.item(row, col)
            if item:
                item.setText(text)
                item.setForeground(_BLACK)

        # WCS indicator – green ✓ if plate-solved, red ✗ otherwise
        wcs_item = self._table.item(row, _C_WCS)
        if wcs_item:
            if hdr.get("has_wcs"):
                wcs_item.setText("✓")
                wcs_item.setForeground(_GREEN)
                wcs_item.setToolTip("Plate-solved: celestial WCS present")
            else:
                wcs_item.setText("✗")
                wcs_item.setForeground(_RED)
                wcs_item.setToolTip("Not plate-solved: no celestial WCS in header")
            wcs_item.setTextAlignment(Qt.AlignCenter)

        _fill(_C_OBJECT,  hdr.get("object",   ""))
        _fill(_C_TYPE,    hdr.get("imagetyp", ""))
        _fill(_C_FILTER,  hdr.get("filter",   ""))

        exptime = hdr.get("exptime")
        _fill(_C_EXPTIME, f"{exptime:9.2f}".strip() if isinstance(exptime, (int, float)) else "")

        xb = hdr.get("xbinning")
        yb = hdr.get("ybinning")
        _fill(_C_BINNING, f"{xb}×{yb}" if xb and yb else "")

        _fill(_C_UT, hdr.get("ut", ""))

    def _on_load_done(self, total: int) -> None:
        self._progress.setVisible(False)
        self._status_lbl.setText(f"{total} image(s) loaded.")
        self._table.setSortingEnabled(True)

    def _stop_loader(self) -> None:
        if self._loader and self._loader.isRunning():
            self._loader.stop()
            self._loader.wait(3000)

    # ── Selection helpers ─────────────────────────────────────────────────────

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for row in range(self._table.rowCount()):
            item = self._table.item(row, _C_CHECK)
            if item:
                item.setCheckState(state)

    def _checked_rows(self) -> list[int]:
        return [
            r for r in range(self._table.rowCount())
            if (item := self._table.item(r, _C_CHECK))
            and item.checkState() == Qt.Checked
        ]

    def _checked_paths(self) -> list[str]:
        paths: list[str] = []
        for row in self._checked_rows():
            item = self._table.item(row, _C_FILENAME)
            if item:
                paths.append(item.data(Qt.UserRole))
        return paths

    def _require_checked(self, action: str) -> list[str] | None:
        paths = self._checked_paths()
        if not paths:
            QMessageBox.information(
                self, action,
                f"Check one or more images before using '{action}'.",
            )
            return None
        return paths

    # ── Action handlers ───────────────────────────────────────────────────────

    def _on_stack(self) -> None:
        from stack import StackThread

        paths = self._require_checked("Stack")
        if not paths:
            return
        if len(paths) < 2:
            QMessageBox.warning(
                self, "Stack",
                "Select at least 2 images to stack.",
            )
            return
        if self._stacker and self._stacker.isRunning():
            QMessageBox.information(
                self, "Stack", "A stack operation is already running."
            )
            return

        # Output path: stacked_<N>frames_<timestamp>.fit in working directory
        ts = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
        out_name = f"stacked_{len(paths)}frames_{ts}.fit"
        out_path = os.path.join(self._working_dir, out_name)

        # Repurpose the existing progress bar for stacking progress
        self._progress.setMaximum(100)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._status_lbl.setText(f"Stacking {len(paths)} image(s) → {out_name}…")
        self._stack_btn.setEnabled(False)

        self._stacker = StackThread(
            file_paths=paths,
            output_path=out_path,
            parent=self,
        )
        self._stacker.progress.connect(
            lambda cur, tot, msg: self._progress.setValue(cur)
        )
        self._stacker.log.connect(self._status_lbl.setText)
        self._stacker.finished.connect(self._on_stack_finished)
        self._stacker.error.connect(self._on_stack_error)
        self._stacker.start()

    def _on_stack_finished(self, output_path: str) -> None:
        self._progress.setVisible(False)
        self._stack_btn.setEnabled(True)
        out_name = os.path.basename(output_path)
        self._status_lbl.setText(f"✓  Stack complete: {out_name}")
        QMessageBox.information(
            self, "Stack Complete",
            f"Stacked image written to:\n{output_path}",
        )
        # Refresh so the new stacked_ file appears in the list
        self._load_images()

    def _on_stack_error(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._stack_btn.setEnabled(True)
        self._status_lbl.setText(f"✗  Stack failed: {msg}")
        QMessageBox.critical(self, "Stack Error", msg)

    def _open_in_system_viewer(self, path: str) -> None:
        """Open *path* with the OS default application for FITS files."""
        url = QUrl.fromLocalFile(path)
        if not QDesktopServices.openUrl(url):
            QMessageBox.warning(
                self, "Cannot Open File",
                f"No application is registered to open FITS files on this system.\n\n"
                f"{path}\n\n"
                "Install a FITS viewer such as DS9, AstroImageJ, or FITS Liberator.",
            )

    def _on_cell_double_clicked(self, row: int, _col: int) -> None:
        """Double-click any cell → open that image in the system FITS viewer."""
        item = self._table.item(row, _C_FILENAME)
        if item:
            path = item.data(Qt.UserRole)
            if path and os.path.isfile(path):
                self._open_in_system_viewer(path)

    def _on_delete(self) -> None:
        paths = self._require_checked("Delete")
        if not paths:
            return

        names = [os.path.basename(p) for p in paths]
        preview = "\n".join(names[:10])
        if len(names) > 10:
            preview += f"\n… and {len(names) - 10} more"
        reply = QMessageBox.question(
            self, "Delete Images",
            f"Permanently delete {len(paths)} image(s) from disk?\n\n{preview}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        failed: list[str] = []
        for path in paths:
            try:
                os.remove(path)
            except OSError as exc:
                failed.append(f"{os.path.basename(path)}: {exc.strerror or exc}")

        deleted = len(paths) - len(failed)
        self._status_lbl.setText(f"Deleted {deleted} image(s).")
        if failed:
            QMessageBox.warning(
                self, "Delete",
                f"Deleted {deleted} image(s); {len(failed)} could not be deleted:\n\n"
                + "\n".join(failed),
            )
        self._load_images()

    def _on_plate_solve(self) -> None:
        from platesolve import find_astap

        paths = self._require_checked("Plate Solve")
        if not paths:
            return
        if self._solver and self._solver.isRunning():
            QMessageBox.information(self, "Plate Solve", "A solve is already running.")
            return

        # Resolve ASTAP path from settings (parent is MainWindow)
        astap = ""
        parent = self.parent()
        if parent and hasattr(parent, "settings"):
            astap = parent.settings.astap_path
        if not astap or not os.path.isfile(astap):
            astap = find_astap() or ""
        if not astap:
            QMessageBox.critical(
                self, "ASTAP Not Found",
                "Could not locate ASTAP.\n\n"
                "Install ASTAP from https://www.hnsky.org/astap.htm\n"
                "then set its path in Settings → Download → Plate Solver."
            )
            return

        # Solve sequentially: queue all paths and solve one at a time
        self._solve_queue = list(paths)
        self._solve_btn.setEnabled(False)
        self._progress.setMaximum(len(paths))
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._solve_results: list = []
        self._astap_path_cached = astap
        self._solve_next()

    def _solve_next(self) -> None:
        from platesolve import PlateSolveThread, ra_dec_from_header

        if not self._solve_queue:
            self._solve_btn.setEnabled(True)
            self._progress.setVisible(False)
            ok  = sum(1 for s, _ in self._solve_results if s)
            fail = len(self._solve_results) - ok
            summary = f"Plate solve complete: {ok} solved"
            if fail:
                summary += f", {fail} failed"
            self._status_lbl.setText(summary)
            self._load_images()   # refresh so new WCS shows in analysis
            return

        fits_path = self._solve_queue.pop(0)
        done = len(self._solve_results)
        self._status_lbl.setText(
            f"Solving {done + 1}/{done + 1 + len(self._solve_queue)}: "
            f"{os.path.basename(fits_path)} …"
        )

        ra_hint, dec_hint = ra_dec_from_header(fits_path)

        self._solver = PlateSolveThread(
            fits_path   = fits_path,
            astap_path  = self._astap_path_cached,
            ra_hint     = ra_hint,
            dec_hint    = dec_hint,
            parent      = self,
        )
        self._solver.log.connect(self._status_lbl.setText)
        self._solver.finished.connect(self._on_solve_result)
        self._solver.start()

    def _on_solve_result(self, success: bool, message: str) -> None:
        self._solve_results.append((success, message))
        self._progress.setValue(len(self._solve_results))
        icon = "✓" if success else "✗"
        self._status_lbl.setText(f"{icon}  {message}")
        if not success:
            QMessageBox.warning(
                self, "Plate Solve Failed",
                message,
            )
        self._solve_next()

    def _on_header(self) -> None:
        paths = self._require_checked("Header")
        if paths:
            QMessageBox.information(
                self, "Header – Not Implemented",
                f"Show header for: {os.path.basename(paths[0])}\n"
                "(Header viewer will be implemented in a future update.)",
            )

    def _on_analyze(self) -> None:
        from photometry import PhotometryThread, format_aavso_report, format_summary
        from report_dialog import PhotometryConfigDialog, ReportDialog

        paths = self._require_checked("Analyze")
        if not paths:
            return
        if len(paths) > 1:
            QMessageBox.information(
                self, "Analyze",
                "Select exactly one image for photometric analysis.\n"
                f"You have {len(paths)} images checked; uncheck all but one.",
            )
            return
        if self._analyzer and self._analyzer.isRunning():
            QMessageBox.information(
                self, "Analyze", "An analysis is already running."
            )
            return

        fits_path = paths[0]

        # Pre-fill sensible defaults from FITS header
        star_from_header = ""
        filt_from_header = "V"
        try:
            from astropy.io import fits as _fits
            with _fits.open(fits_path) as hdul:
                star_from_header = str(hdul[0].header.get("OBJECT", "")).strip()
                filt_from_header = str(hdul[0].header.get("FILTER", "V")).strip() or "V"
        except Exception:
            pass

        # Ask the user to confirm / tweak settings
        main_settings = getattr(self.parent(), "settings", None)
        observer_default = main_settings.observer_code if main_settings else ""

        cfg = PhotometryConfigDialog(
            star_name=star_from_header,
            filter_band=filt_from_header,
            observer_code=observer_default or "UNKNOWN",
            parent=self,
        )
        if not cfg.exec():
            return

        if main_settings is not None and cfg.observer_code != observer_default:
            main_settings.observer_code = cfg.observer_code
            main_settings.sync()

        # Progress bar reused from the panel
        self._progress.setMaximum(0)  # indeterminate while each step runs
        self._progress.setVisible(True)
        self._status_lbl.setText(f"Analysing {os.path.basename(fits_path)} …")
        self._analyze_btn.setEnabled(False)

        self._analyzer = PhotometryThread(
            fits_path       = fits_path,
            star_name       = cfg.star_name or None,
            filter_band     = cfg.filter_band,
            comp_mag_min    = cfg.comp_mag_min,
            comp_mag_max    = cfg.comp_mag_max,
            aperture_radius = cfg.aperture_radius,
            snr_threshold   = cfg.snr_threshold,
            observer_code   = cfg.observer_code,
            log_dir         = self._working_dir,
            parent          = self,
        )
        self._analyzer.status.connect(self._status_lbl.setText)
        self._analyzer.finished.connect(
            lambda result, _fa=format_aavso_report, _fs=format_summary,
                   _RD=ReportDialog:
            self._on_analysis_finished(result, _fa, _fs, _RD)
        )
        self._analyzer.error.connect(self._on_analysis_error)
        self._analyzer.start()

    def _on_analysis_finished(self, result, format_aavso_report, format_summary, ReportDialog) -> None:
        self._progress.setVisible(False)
        self._analyze_btn.setEnabled(True)
        report = format_aavso_report(result) + "\n" + format_summary(result)
        main_settings = getattr(self.parent(), "settings", None)
        lightcurve_days = main_settings.lightcurve_days if main_settings else 10
        dlg = ReportDialog(
            report_text = report,
            log_path    = result.fits_path and (
                # Look for the matching log in the working directory
                next(
                    (os.path.join(self._working_dir, f)
                     for f in os.listdir(self._working_dir)
                     if f.startswith("photometry_") and f.endswith(".log")
                     and result.star_name.replace(" ", "") in f),
                    None,
                )
            ),
            star_name       = result.star_name,
            filter_band     = result.filter_band,
            target_jd       = result.jd,
            target_mag      = result.target_mag,
            mag_error       = result.mag_error,
            lightcurve_days = lightcurve_days,
            parent=self,
        )
        self._status_lbl.setText(
            f"✓  {result.star_name}  {result.filter_band} = {result.target_mag:.3f} "
            f"± {result.mag_error:.3f}"
        )
        dlg.exec()

    def _on_analysis_error(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._analyze_btn.setEnabled(True)
        self._status_lbl.setText(f"✗  Analysis failed: {msg}")
        QMessageBox.critical(self, "Analysis Error", msg)

    # ── Exposure calibration ──────────────────────────────────────────────────

    def _on_calibrate(self) -> None:
        from exposure import CalibrationThread

        paths = self._require_checked("Calibrate Exposure")
        if not paths:
            return
        if len(paths) > 1:
            QMessageBox.information(
                self, "Calibrate Exposure",
                "Select exactly one plate-solved image to calibrate from.\n"
                f"You have {len(paths)} images checked; uncheck all but one.",
            )
            return
        if self._calibrator and self._calibrator.isRunning():
            QMessageBox.information(
                self, "Calibrate Exposure", "A calibration is already running."
            )
            return

        self._progress.setMaximum(0)  # indeterminate
        self._progress.setVisible(True)
        self._calibrate_btn.setEnabled(False)

        self._calibrator = CalibrationThread(paths[0], parent=self)
        self._calibrator.status.connect(self._status_lbl.setText)
        self._calibrator.succeeded.connect(self._on_calibration_done)
        self._calibrator.error.connect(self._on_calibration_error)
        self._calibrator.start()

    def _on_calibration_done(self, calib) -> None:
        from exposure import suggest_exposure

        self._progress.setVisible(False)
        self._calibrate_btn.setEnabled(True)

        # Persist via the main window's SettingsManager
        parent = self.parent()
        saved = ""
        if parent is not None and hasattr(parent, "settings"):
            parent.settings.save_exposure_calibration(calib.to_dict())
            parent.settings.sync()
            saved = "\nSaved — the Targets panel can now suggest exposures."

        preview_lines = []
        for mag in (10.0, 12.0, 14.0, 16.0):
            s = suggest_exposure(calib, faint_mag=mag)
            note = "  (comp-star / max limit)" if s.capped_by_comp else ""
            preview_lines.append(
                f"  mag {mag:.0f} → {s.seconds}s  (S/N ≈ {s.expected_snr:.0f}){note}"
            )

        self._status_lbl.setText(
            f"✓  Calibrated {calib.telescope}/{calib.filter_band}  "
            f"ZP={calib.zeropoint:.2f} ±{calib.zp_rms:.2f}"
        )
        QMessageBox.information(
            self, "Exposure Calibration",
            f"Telescope    : {calib.telescope}\n"
            f"Filter       : {calib.filter_band}\n"
            f"Zeropoint    : {calib.zeropoint:.2f} ± {calib.zp_rms:.2f} mag "
            f"({calib.n_stars} comp stars)\n"
            f"Sky          : {calib.sky_rate:.2f} ADU/s/px\n"
            f"Source image : {calib.source_image}\n\n"
            f"Suggested exposures (S/N 50):\n" + "\n".join(preview_lines) + f"\n{saved}",
        )

    def _on_calibration_error(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._calibrate_btn.setEnabled(True)
        self._status_lbl.setText(f"✗  Calibration failed: {msg}")
        QMessageBox.critical(self, "Calibration Error", msg)

    # ── Window close ──────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_loader()
        event.accept()
