"""Report dialog: display AAVSO WebObs report, save to file, and analysis config."""
from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont

# ── Matplotlib (optional – graceful fallback if missing) ──────────────────────

try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg  # type: ignore
    from matplotlib.figure import Figure  # type: ignore
    _MPL_OK = True
except ImportError:
    _MPL_OK = False


# ── Configuration dialog (shown before running photometry) ────────────────────

class PhotometryConfigDialog(QDialog):
    """Collect per-run settings before starting photometric analysis."""

    def __init__(
        self,
        star_name: str = "",
        filter_band: str = "V",
        observer_code: str = "UNKNOWN",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Photometry Analysis – Settings")
        self.setMinimumWidth(420)
        self._build_ui(star_name, filter_band, observer_code)

    def _build_ui(
        self, star_name: str, filter_band: str, observer_code: str
    ) -> None:
        root = QVBoxLayout(self)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self._star_name = QLineEdit(star_name)
        self._star_name.setPlaceholderText("e.g. RW AUR")
        form.addRow("Star name:", self._star_name)

        self._filter = QLineEdit(filter_band)
        self._filter.setPlaceholderText("V, B, R, I …")
        form.addRow("Filter:", self._filter)

        self._observer = QLineEdit(observer_code)
        self._observer.setPlaceholderText("AAVSO observer code")
        form.addRow("Observer code:", self._observer)

        root.addLayout(form)

        box = QGroupBox("Comparison stars && aperture")
        box_form = QFormLayout(box)

        self._comp_min = QDoubleSpinBox()
        self._comp_min.setRange(5.0, 20.0)
        self._comp_min.setValue(11.0)
        self._comp_min.setSingleStep(0.5)
        box_form.addRow("Brightest comp mag:", self._comp_min)

        self._comp_max = QDoubleSpinBox()
        self._comp_max.setRange(5.0, 20.0)
        self._comp_max.setValue(13.5)
        self._comp_max.setSingleStep(0.5)
        box_form.addRow("Dimmest comp mag:", self._comp_max)

        self._aperture = QDoubleSpinBox()
        self._aperture.setRange(2.0, 50.0)
        self._aperture.setValue(6.0)
        self._aperture.setSingleStep(1.0)
        self._aperture.setSuffix(" px")
        box_form.addRow("Aperture radius:", self._aperture)

        self._snr = QDoubleSpinBox()
        self._snr.setRange(1.0, 50.0)
        self._snr.setValue(5.0)
        self._snr.setSingleStep(1.0)
        box_form.addRow("Source SNR threshold:", self._snr)

        root.addWidget(box)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    # ── Accessors ─────────────────────────────────────────────────────────────

    @property
    def star_name(self) -> str:
        return self._star_name.text().strip()

    @property
    def filter_band(self) -> str:
        return self._filter.text().strip() or "V"

    @property
    def observer_code(self) -> str:
        return self._observer.text().strip() or "UNKNOWN"

    @property
    def comp_mag_min(self) -> float:
        return self._comp_min.value()

    @property
    def comp_mag_max(self) -> float:
        return self._comp_max.value()

    @property
    def aperture_radius(self) -> float:
        return self._aperture.value()

    @property
    def snr_threshold(self) -> float:
        return self._snr.value()


# ── Results / report dialog ───────────────────────────────────────────────────

class ReportDialog(QDialog):
    """Display the AAVSO WebObs report, analysis log, and light curve.

    * Report && Log tab – the AAVSO Extended-format report (ready for WebObs
      upload) alongside the full analysis log from the working-directory
      .log file.
    * Light Curve tab – AAVSO archive observations for the target/filter
      (green) over the configured lookback window, plus this measurement
      (red).
    """

    def __init__(
        self,
        report_text: str,
        log_path: str | None = None,
        star_name: str = "",
        filter_band: str = "V",
        target_jd: float = 0.0,
        target_mag: float = 0.0,
        mag_error: float = 0.0,
        lightcurve_days: int = 10,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._report_text     = report_text
        self._log_path        = log_path
        self._star_name       = star_name
        self._filter_band     = filter_band
        self._target_jd       = target_jd
        self._target_mag      = target_mag
        self._mag_error       = mag_error
        self._lightcurve_days = lightcurve_days
        self._lc_thread       = None
        self.setWindowTitle("Photometry Report")
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowMinimizeButtonHint
        )
        self.resize(980, 620)
        self._build_ui()
        self._start_lightcurve_fetch()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        tabs = QTabWidget()
        tabs.addTab(self._build_report_tab(), "Report && Log")
        tabs.addTab(self._build_lightcurve_tab(), "Light Curve")
        root.addWidget(tabs, 1)

        # Action buttons
        btn_row = self._build_button_row()
        root.addLayout(btn_row)

    def _build_report_tab(self) -> QWidget:
        tab = QWidget()
        panels = QHBoxLayout(tab)

        mono = QFont("Consolas", 9)
        mono.setStyleHint(QFont.Monospace)

        # Left: AAVSO report
        left = QVBoxLayout()
        left.addWidget(QLabel("<b>AAVSO WebObs Extended Format</b>"))
        self._report_edit = QTextEdit()
        self._report_edit.setReadOnly(True)
        self._report_edit.setFont(mono)
        self._report_edit.setPlainText(self._report_text)
        left.addWidget(self._report_edit, 1)
        panels.addLayout(left, 1)

        # Right: analysis log
        right = QVBoxLayout()
        log_hdr_row = QHBoxLayout()
        log_hdr_row.addWidget(QLabel("<b>Analysis Log</b>"))
        if self._log_path:
            path_lbl = QLabel(os.path.basename(self._log_path))
            path_lbl.setStyleSheet("color: gray; font-size: 11px;")
            path_lbl.setToolTip(self._log_path)
            log_hdr_row.addWidget(path_lbl)
        log_hdr_row.addStretch()
        right.addLayout(log_hdr_row)

        self._log_edit = QTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setFont(mono)
        if self._log_path and os.path.isfile(self._log_path):
            try:
                with open(self._log_path, encoding="utf-8") as fh:
                    self._log_edit.setPlainText(fh.read())
            except OSError:
                self._log_edit.setPlainText("(Log file could not be read)")
        else:
            self._log_edit.setPlainText("(No log file)")
        right.addWidget(self._log_edit, 1)
        panels.addLayout(right, 1)

        return tab

    def _build_lightcurve_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        self._lc_status = QLabel("")
        self._lc_status.setStyleSheet("color: gray;")
        layout.addWidget(self._lc_status)

        if _MPL_OK:
            self._lc_figure = Figure(figsize=(6, 4))
            self._lc_canvas = FigureCanvasQTAgg(self._lc_figure)
            layout.addWidget(self._lc_canvas, 1)
        else:
            self._lc_figure = None
            self._lc_canvas = None
            layout.addWidget(QLabel(
                "matplotlib is not installed – the light-curve chart is "
                "unavailable.\nInstall it with:  pip install matplotlib"
            ), 1)

        return tab

    def _build_button_row(self) -> QHBoxLayout:
        btn_row = QHBoxLayout()

        save_report_btn = QPushButton("💾 Save Report…")
        save_report_btn.setToolTip("Save the AAVSO WebObs text file for upload")
        save_report_btn.clicked.connect(self._save_report)

        save_log_btn = QPushButton("📄 Save Log…")
        save_log_btn.setToolTip("Save a copy of the analysis log")
        save_log_btn.clicked.connect(self._save_log)

        webobs_btn = QPushButton("↗ Open WebObs…")
        webobs_btn.setToolTip("Open the AAVSO WebObs submission page in your browser")
        webobs_btn.clicked.connect(self._open_webobs)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)

        btn_row.addWidget(save_report_btn)
        btn_row.addWidget(save_log_btn)
        btn_row.addWidget(webobs_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        return btn_row

    # ── Light curve chart ─────────────────────────────────────────────────────

    def _start_lightcurve_fetch(self) -> None:
        if not self._star_name:
            self._lc_status.setText("No star name available – light curve skipped.")
            self._plot_lightcurve([])
            return

        from lightcurve import LightCurveThread

        from_jd = self._target_jd - self._lightcurve_days
        to_jd   = self._target_jd + 0.5

        self._lc_status.setText(
            f"Downloading AAVSO archive observations for {self._star_name} "
            f"({self._filter_band}, last {self._lightcurve_days} days)…"
        )
        self._lc_thread = LightCurveThread(
            star_name   = self._star_name,
            filter_band = self._filter_band,
            from_jd     = from_jd,
            to_jd       = to_jd,
            parent      = self,
        )
        self._lc_thread.finished.connect(self._on_lightcurve_ready)
        self._lc_thread.error.connect(self._on_lightcurve_error)
        self._lc_thread.start()

    def _on_lightcurve_ready(self, points: list) -> None:
        if points:
            self._lc_status.setText(
                f"{len(points)} AAVSO {self._filter_band}-band observation(s) "
                f"in the last {self._lightcurve_days} days."
            )
        else:
            self._lc_status.setText(
                f"No AAVSO {self._filter_band}-band observations found in the "
                f"last {self._lightcurve_days} days."
            )
        self._plot_lightcurve(points)

    def _on_lightcurve_error(self, msg: str) -> None:
        self._lc_status.setText(f"Could not download AAVSO light curve: {msg}")
        self._plot_lightcurve([])

    def _plot_lightcurve(self, points: list) -> None:
        if not _MPL_OK:
            return

        self._lc_figure.clear()
        ax = self._lc_figure.add_subplot(111)

        if points:
            jds  = [p.jd for p in points]
            mags = [p.mag for p in points]
            ax.scatter(jds, mags, c="green", s=24, label="AAVSO observations", zorder=2)

        if self._target_jd:
            ax.scatter(
                [self._target_jd], [self._target_mag],
                c="red", s=70, label="This measurement",
                edgecolors="black", linewidths=0.5, zorder=3,
            )

        ax.invert_yaxis()
        ax.set_xlabel("Julian Date")
        ax.set_ylabel("Magnitude")
        ax.set_title(f"{self._star_name} – {self._filter_band} band")
        if points or self._target_jd:
            ax.legend(loc="best", fontsize=9)
        ax.grid(True, alpha=0.3)
        self._lc_figure.tight_layout()
        self._lc_canvas.draw()

    # ── Button handlers ───────────────────────────────────────────────────────

    def _save_report(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save AAVSO Report", "",
            "Text Files (*.txt);;All Files (*)",
        )
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self._report_text)
            QMessageBox.information(self, "Saved", f"Report saved to:\n{path}")

    def _save_log(self) -> None:
        default = os.path.basename(self._log_path) if self._log_path else "photometry.log"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Analysis Log", default,
            "Log Files (*.log);;Text Files (*.txt);;All Files (*)",
        )
        if path:
            content = self._log_edit.toPlainText()
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            QMessageBox.information(self, "Saved", f"Log saved to:\n{path}")

    def _open_webobs(self) -> None:
        QDesktopServices.openUrl(
            QUrl("https://www.aavso.org/webobs")
        )
