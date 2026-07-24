"""Photometric transformation-coefficient generator dialog.

Lets the user pick a telescope, an AAVSO standard field, and one
plate-solved FITS image per filter; runs the transform calculation off
the UI thread; displays the resulting coefficients with per-transform
interactive outlier review; and saves the results into the telescope's
row in the ``telescopes`` / ``telescope_coefficients`` database tables.
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)

from transform_generator import (
    LANDOLT_FIELD,
    STANDARD_FIELD_NAMES,
    TransformGeneratorThread,
    TransformRunResult,
)

FITS_FILTER = "FITS Files (*.fit *.fits *.fts);;All Files (*)"
_FILTER_LETTERS = ["U", "B", "V", "R", "I"]


class TransformGeneratorDialog(QDialog):
    """Modal dialog: configure a run, calculate, review, and save coefficients."""

    def __init__(self, db, parent=None) -> None:
        super().__init__(parent)
        self._db = db
        self._worker: TransformGeneratorThread | None = None
        self._run_result: TransformRunResult | None = None
        self._image_edits: dict[str, QLineEdit] = {}

        self.setWindowTitle("Transformation Coefficient Generator")
        self.resize(760, 640)
        self._build_ui()
        self._load_telescopes()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self._telescope_combo = QComboBox()
        form.addRow("Telescope:", self._telescope_combo)

        self._field_combo = QComboBox()
        self._field_combo.addItems(STANDARD_FIELD_NAMES)
        form.addRow("Standard field:", self._field_combo)

        self._snr = QDoubleSpinBox()
        self._snr.setRange(1.0, 50.0)
        self._snr.setValue(5.0)
        form.addRow("Source SNR threshold:", self._snr)

        self._aperture = QDoubleSpinBox()
        self._aperture.setRange(2.0, 50.0)
        self._aperture.setValue(6.0)
        self._aperture.setSuffix(" px")
        form.addRow("Aperture radius:", self._aperture)

        self._field_radius = QDoubleSpinBox()
        self._field_radius.setRange(1.0, 180.0)
        self._field_radius.setValue(30.0)
        self._field_radius.setSuffix(" arcmin")
        self._field_radius.setToolTip(
            "Only match reference stars within this radius of the field "
            "centre. Keep this close to the standard field's actual size "
            "(commonly 20-30') — wide-field images have less accurate WCS "
            "far from centre, so matching against the full ~3 deg catalog "
            "query radius can miss every star."
        )
        form.addRow("Matching field radius:", self._field_radius)

        self._match_tol = QDoubleSpinBox()
        self._match_tol.setRange(1.0, 100.0)
        self._match_tol.setValue(5.0)
        self._match_tol.setSuffix(" px")
        self._match_tol.setToolTip(
            "How close (in pixels) a catalog star's WCS-projected position "
            "must fall to a detected source to count as a match. Raise this "
            "if the log reports a nearest-source distance larger than the "
            "tolerance for most stars."
        )
        form.addRow("Match tolerance:", self._match_tol)

        root.addLayout(form)

        landolt_hint = QLabel(
            "Landolt Field: RA/Dec is read from the field centre of the first "
            "filter image supplied below."
        )
        landolt_hint.setWordWrap(True)
        landolt_hint.setStyleSheet("color: gray; font-size: 11px;")
        root.addWidget(landolt_hint)

        img_box = QGroupBox("Filter Images (one plate-solved FITS per filter; at least 2 required)")
        img_form = QFormLayout(img_box)
        img_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        for letter in _FILTER_LETTERS:
            edit = QLineEdit()
            edit.setPlaceholderText("(not supplied)")
            browse = QPushButton("Browse…")
            browse.setFixedWidth(80)
            browse.clicked.connect(lambda _checked=False, letter=letter: self._browse_image(letter))
            row = QHBoxLayout()
            row.addWidget(edit)
            row.addWidget(browse)
            img_form.addRow(f"{letter}:", row)
            self._image_edits[letter] = edit
        root.addWidget(img_box)

        btn_row = QHBoxLayout()
        self._calc_btn = QPushButton("Calculate Transforms")
        self._calc_btn.clicked.connect(self._on_calculate)
        btn_row.addWidget(self._calc_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color: #555; font-size: 11px;")
        self._status_lbl.setWordWrap(True)
        root.addWidget(self._status_lbl)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Consolas", 9))
        self._log.setFixedHeight(120)
        root.addWidget(self._log)

        self._results_table = QTableWidget(0, 4)
        self._results_table.setHorizontalHeaderLabels(["Transform", "Value", "Error", "R²"])
        self._results_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._results_table.setSelectionBehavior(QTableWidget.SelectRows)
        hh = self._results_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Stretch)
        self._results_table.cellDoubleClicked.connect(self._on_review_row)
        root.addWidget(self._results_table, 1)

        review_hint = QLabel("Double-click a row to review/reject outlier points.")
        review_hint.setStyleSheet("color: gray; font-size: 11px;")
        root.addWidget(review_hint)

        bottom_row = QHBoxLayout()
        self._save_btn = QPushButton("Save to Telescope")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._on_save)
        bottom_row.addWidget(self._save_btn)
        bottom_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        bottom_row.addWidget(close_btn)
        root.addLayout(bottom_row)

    # ── Loading ───────────────────────────────────────────────────────────────

    def _load_telescopes(self) -> None:
        names = sorted(self._db.load_telescopes().keys())
        self._telescope_combo.addItems(names)
        if not names:
            self._status_lbl.setText(
                "No telescopes defined yet — add one via Edit → Telescopes… first."
            )
            self._calc_btn.setEnabled(False)

    def _browse_image(self, letter: str) -> None:
        current = self._image_edits[letter].text().strip()
        start = os.path.dirname(current) if current else ""
        path, _ = QFileDialog.getOpenFileName(
            self, f"Select {letter}-filter Image", start, FITS_FILTER
        )
        if path:
            self._image_edits[letter].setText(path)

    # ── Calculate ──────────────────────────────────────────────────────────────

    def _on_calculate(self) -> None:
        telescope = self._telescope_combo.currentText().strip()
        if not telescope:
            QMessageBox.information(self, "Calculate Transforms", "Select a telescope first.")
            return

        filter_images = {
            letter: edit.text().strip()
            for letter, edit in self._image_edits.items()
            if edit.text().strip()
        }
        if len(filter_images) < 2:
            QMessageBox.information(
                self, "Calculate Transforms",
                "Supply at least two filter images to compute a transform.",
            )
            return

        std_field = self._field_combo.currentText()
        if std_field == LANDOLT_FIELD and not filter_images:
            QMessageBox.information(
                self, "Calculate Transforms",
                "A Landolt field needs at least one filter image to determine its RA/Dec.",
            )
            return

        self._calc_btn.setEnabled(False)
        self._save_btn.setEnabled(False)
        self._results_table.setRowCount(0)
        self._run_result = None
        self._progress.setMaximum(0)
        self._progress.setVisible(True)
        self._status_lbl.setText("Starting…")
        self._log.clear()

        self._worker = TransformGeneratorThread(
            std_field_name       = std_field,
            filter_images        = filter_images,
            snr_threshold        = self._snr.value(),
            aperture_radius      = self._aperture.value(),
            field_radius_arcmin  = self._field_radius.value(),
            match_tol_px         = self._match_tol.value(),
            parent               = self,
        )
        self._worker.status.connect(self._on_status)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_status(self, message: str) -> None:
        self._status_lbl.setText(message)
        self._log.append(message)

    def _on_finished(self, result: TransformRunResult) -> None:
        self._progress.setVisible(False)
        self._calc_btn.setEnabled(True)
        self._run_result = result
        self._populate_results(result)
        self._save_btn.setEnabled(True)
        self._status_lbl.setText(
            f"✓  {result.num_reference_stars} reference stars, "
            f"{len(result.available_filters)} filter(s) matched, "
            f"{len(result.transforms)} transform(s) computed."
        )

    def _on_error(self, message: str) -> None:
        self._progress.setVisible(False)
        self._calc_btn.setEnabled(True)
        self._status_lbl.setText(f"✗  {message}")
        self._log.append(f"✗  {message}")
        QMessageBox.warning(self, "Calculate Transforms", message)

    # ── Results table ──────────────────────────────────────────────────────────

    def _populate_results(self, result: TransformRunResult) -> None:
        names = sorted(result.transforms)
        self._results_table.setRowCount(len(names))
        for row, name in enumerate(names):
            t = result.transforms[name]
            values = [name, f"{t.value:.4f}", f"{t.error:.4f}", f"{t.r_squared:.3f}"]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self._results_table.setItem(row, col, item)

    def _on_review_row(self, row: int, _col: int) -> None:
        if self._run_result is None:
            return
        name_item = self._results_table.item(row, 0)
        if name_item is None:
            return
        result = self._run_result.transforms.get(name_item.text())
        if result is None:
            return
        from transform_review_dialog import TransformReviewDialog

        TransformReviewDialog(result, parent=self).exec()
        self._populate_results(self._run_result)

    # ── Save ───────────────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        if self._run_result is None:
            return
        telescope = self._telescope_combo.currentText().strip()
        new_coeffs = {name: t.value for name, t in self._run_result.transforms.items()}

        telescopes = self._db.load_telescopes()
        existing = telescopes.get(telescope, {})
        preview = "\n".join(
            f"  {name}: {existing.get(name, '—')}  →  {value:.4f}"
            for name, value in sorted(new_coeffs.items())
        )
        reply = QMessageBox.question(
            self, "Save to Telescope",
            f"Save {len(new_coeffs)} coefficient(s) to '{telescope}'?\n\n{preview}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        existing.update(new_coeffs)
        telescopes[telescope] = existing
        self._db.save_telescopes(telescopes)
        QMessageBox.information(
            self, "Save to Telescope",
            f"Saved {len(new_coeffs)} coefficient(s) to '{telescope}'.",
        )
