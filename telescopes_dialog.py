"""Telescope table editor: photometric transformation coefficients per telescope.

Provides CRUD over the ``telescopes`` / ``telescope_coefficients`` database
tables.  Rows are telescopes; columns are the standard AAVSO BVRI
transformation coefficients.  Edits auto-save to the database on every
change, matching the observation plan's persistence behaviour.

The coefficients are stored for use in transformed photometry; the current
photometry pipeline reports untransformed magnitudes (TRANS=NO).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

# Standard AAVSO CCD transformation coefficients (UBVRI).
# Colour slopes first, then per-band magnitude coefficients.  Matches the
# full transform set the Transform Generator (transform_generator.py) can
# compute — see its TRANSFORM_DEFS.
COEFFICIENTS: list[str] = [
    "Tub", "Tbv", "Tbr", "Tbi", "Tvr", "Tri", "Tvi",
    "Tu_ub", "Tb_ub", "Tb_bv", "Tv_bv", "Tb_br", "Tb_bi",
    "Tv_vr", "Tr_vr", "Tr_ri", "Ti_ri", "Tv_vi", "Ti_vi", "Tr_vi",
]

_COEFF_TOOLTIPS: dict[str, str] = {
    "Tub":    "Slope of standard (U−B) vs instrumental (u−b)",
    "Tbv":    "Slope of standard (B−V) vs instrumental (b−v)",
    "Tbr":    "Slope of standard (B−R) vs instrumental (b−r)",
    "Tbi":    "Slope of standard (B−I) vs instrumental (b−i)",
    "Tvr":    "Slope of standard (V−R) vs instrumental (v−r)",
    "Tri":    "Slope of standard (R−I) vs instrumental (r−i)",
    "Tvi":    "Slope of standard (V−I) vs instrumental (v−i)",
    "Tu_ub":  "U-magnitude coefficient vs (U−B) colour",
    "Tb_ub":  "B-magnitude coefficient vs (U−B) colour",
    "Tb_bv":  "B-magnitude coefficient vs (B−V) colour",
    "Tv_bv":  "V-magnitude coefficient vs (B−V) colour",
    "Tb_br":  "B-magnitude coefficient vs (B−R) colour",
    "Tb_bi":  "B-magnitude coefficient vs (B−I) colour",
    "Tv_vr":  "V-magnitude coefficient vs (V−R) colour",
    "Tr_vr":  "R-magnitude coefficient vs (V−R) colour",
    "Tr_ri":  "R-magnitude coefficient vs (R−I) colour",
    "Ti_ri":  "I-magnitude coefficient vs (R−I) colour",
    "Tv_vi":  "V-magnitude coefficient vs (V−I) colour",
    "Ti_vi":  "I-magnitude coefficient vs (V−I) colour",
    "Tr_vi":  "R-magnitude coefficient vs (V−I) colour",
}

_C_NAME = 0  # telescope-name column; coefficients start at column 1


class TelescopesDialog(QDialog):
    """Modal dialog for managing telescopes and their transformation coefficients."""

    def __init__(self, db, parent=None) -> None:
        super().__init__(parent)
        self._db = db
        self._populating = False

        self.setWindowTitle("Telescopes – Transformation Coefficients")
        self.resize(900, 380)

        self._build_ui()
        self._load()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        info = QLabel(
            "Transformation coefficients per telescope (AAVSO BVRI set). "
            "Leave a cell blank when a coefficient has not been measured. "
            "Changes are saved automatically."
        )
        info.setWordWrap(True)
        info.setStyleSheet("font-size: 11px; color: #555;")
        root.addWidget(info)

        headers = ["Telescope"] + COEFFICIENTS
        self._table = QTableWidget(0, len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        for col, coeff in enumerate(COEFFICIENTS, start=1):
            item = self._table.horizontalHeaderItem(col)
            if item:
                item.setToolTip(_COEFF_TOOLTIPS.get(coeff, ""))
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)

        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        self._table.setColumnWidth(_C_NAME, 130)
        for col in range(1, len(headers)):
            self._table.setColumnWidth(col, 72)

        self._table.cellChanged.connect(self._on_cell_changed)
        root.addWidget(self._table, 1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Telescope…")
        add_btn.clicked.connect(self._on_add)
        del_btn = QPushButton("Delete Telescope")
        del_btn.clicked.connect(self._on_delete)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)

        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── Loading ───────────────────────────────────────────────────────────────

    def _load(self) -> None:
        telescopes = self._db.load_telescopes()
        self._populating = True
        try:
            self._table.setRowCount(len(telescopes))
            for row, (name, coeffs) in enumerate(telescopes.items()):
                self._set_row(row, name, coeffs)
        finally:
            self._populating = False

    def _set_row(self, row: int, name: str, coeffs: dict[str, float]) -> None:
        name_item = QTableWidgetItem(name)
        # Remember the last-saved text so invalid edits can be reverted
        name_item.setData(Qt.UserRole, name)
        self._table.setItem(row, _C_NAME, name_item)
        for col, coeff in enumerate(COEFFICIENTS, start=1):
            text = f"{coeffs[coeff]:g}" if coeff in coeffs else ""
            item = QTableWidgetItem(text)
            item.setData(Qt.UserRole, text)
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._table.setItem(row, col, item)

    # ── Editing / auto-save ───────────────────────────────────────────────────

    def _on_cell_changed(self, row: int, col: int) -> None:
        if self._populating:
            return
        item = self._table.item(row, col)
        if item is None:
            return

        text = item.text().strip()
        if col == _C_NAME:
            others = {
                self._table.item(r, _C_NAME).text().strip()
                for r in range(self._table.rowCount())
                if r != row and self._table.item(r, _C_NAME)
            }
            if not text or text in others:
                reason = "empty" if not text else "already in use"
                QMessageBox.warning(
                    self, "Telescope Name",
                    f"Telescope name is {reason}; reverting.",
                )
                self._revert(item)
                return
        else:
            if text:
                try:
                    value = float(text)
                except ValueError:
                    QMessageBox.warning(
                        self, "Invalid Value",
                        f"'{text}' is not a number.  Enter a decimal "
                        "coefficient (e.g. -0.052) or leave the cell blank.",
                    )
                    self._revert(item)
                    return
                text = f"{value:g}"

        self._populating = True
        try:
            item.setText(text)
            item.setData(Qt.UserRole, text)
        finally:
            self._populating = False
        self._save()

    def _revert(self, item: QTableWidgetItem) -> None:
        self._populating = True
        try:
            item.setText(str(item.data(Qt.UserRole) or ""))
        finally:
            self._populating = False

    def _save(self) -> None:
        telescopes: dict[str, dict[str, float]] = {}
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, _C_NAME)
            if name_item is None or not name_item.text().strip():
                continue
            coeffs: dict[str, float] = {}
            for col, coeff in enumerate(COEFFICIENTS, start=1):
                item = self._table.item(row, col)
                if item and item.text().strip():
                    coeffs[coeff] = float(item.text())
            telescopes[name_item.text().strip()] = coeffs
        self._db.save_telescopes(telescopes)

    # ── Add / delete ──────────────────────────────────────────────────────────

    def _on_add(self) -> None:
        name, ok = QInputDialog.getText(
            self, "Add Telescope", "Telescope name (e.g. T05):"
        )
        name = name.strip()
        if not ok or not name:
            return
        for row in range(self._table.rowCount()):
            item = self._table.item(row, _C_NAME)
            if item and item.text().strip() == name:
                QMessageBox.information(
                    self, "Add Telescope", f"'{name}' already exists."
                )
                return

        row = self._table.rowCount()
        self._populating = True
        try:
            self._table.setRowCount(row + 1)
            self._set_row(row, name, {})
        finally:
            self._populating = False
        self._save()
        self._table.setCurrentCell(row, _C_NAME)

    def _on_delete(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            QMessageBox.information(
                self, "Delete Telescope", "Select a telescope row first."
            )
            return
        name_item = self._table.item(row, _C_NAME)
        name = name_item.text() if name_item else ""
        reply = QMessageBox.question(
            self, "Delete Telescope",
            f"Delete '{name}' and its transformation coefficients?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._table.removeRow(row)
        self._save()
