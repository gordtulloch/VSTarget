"""Main application window: Variable List, Targets panel, and script export."""
from __future__ import annotations

from typing import List, Optional, Set

from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    QThread,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QFont, QKeySequence

from aavso_client import FetchTargetsThread
from models import AAVSOTarget, ObservingTarget, SECTION_CODES
from script_exporter import ScriptExporter
from settings_dialog import SettingsDialog
from settings_manager import SettingsManager


# ─────────────────────────────────────────────────────────────────────────────
# Variable List model + proxy
# ─────────────────────────────────────────────────────────────────────────────

class VariableListModel(QAbstractTableModel):
    """Read-only model displaying AAVSO variable star targets."""

    HEADERS = [
        "★", "Name", "Type", "Max Mag", "Min Mag",
        "Filters", "RA (h)", "Dec (°)", "Const.", "Cadence (d)",
    ]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._data: List[AAVSOTarget] = []

    # ── Public API ────────────────────────────────────────────────────────────

    def set_targets(self, targets: List[AAVSOTarget]) -> None:
        self.beginResetModel()
        self._data = targets
        self.endResetModel()

    def target_at(self, row: int) -> Optional[AAVSOTarget]:
        return self._data[row] if 0 <= row < len(self._data) else None

    # ── QAbstractTableModel interface ─────────────────────────────────────────

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._data)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        t = self._data[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            if col == 0:
                return "★" if t.priority else ""
            elif col == 1:
                return t.star_name
            elif col == 2:
                return t.var_type
            elif col == 3:
                if t.max_mag is not None:
                    band = f" {t.max_mag_band}" if t.max_mag_band else ""
                    return f"{t.max_mag:.1f}{band}"
                return ""
            elif col == 4:
                if t.min_mag is not None:
                    band = f" {t.min_mag_band}" if t.min_mag_band else ""
                    return f"{t.min_mag:.1f}{band}"
                return ""
            elif col == 5:
                return t.filters
            elif col == 6:
                return f"{t.ra_hours:.4f}"
            elif col == 7:
                return f"{t.dec:+.4f}"
            elif col == 8:
                return t.constellation
            elif col == 9:
                return f"{t.obs_cadence:.1f}" if t.obs_cadence is not None else ""

        elif role == Qt.ForegroundRole:
            if t.solar_conjunction:
                return QBrush(QColor("#999999"))
            if t.priority:
                return QBrush(QColor("#b87820"))

        elif role == Qt.ToolTipRole:
            parts: List[str] = [f"<b>{t.star_name}</b> ({t.var_type})"]
            if t.period is not None:
                parts.append(f"Period: {t.period:.4f} d")
            if t.obs_cadence is not None:
                parts.append(f"Cadence: {t.obs_cadence:.1f} d")
            if t.obs_mode:
                parts.append(f"Mode: {t.obs_mode}")
            if t.obs_section:
                parts.append(f"Section: {', '.join(t.obs_section)}")
            if t.solar_conjunction:
                parts.append("<i>⚠ Near solar conjunction – may not be observable</i>")
            if t.other_info:
                # Strip [[description url]] wiki-link markup for tooltip
                info = t.other_info
                parts.append(info)
            return "<br>".join(parts)

        # Provide raw numeric values so the sort proxy can sort correctly
        elif role == Qt.UserRole:
            if col == 0:
                return 0 if t.priority else 1   # sort priorities first
            elif col == 3:
                return t.max_mag if t.max_mag is not None else 99.0
            elif col == 4:
                return t.min_mag if t.min_mag is not None else 99.0
            elif col == 6:
                return t.ra_hours
            elif col == 7:
                return t.dec
            elif col == 9:
                return t.obs_cadence if t.obs_cadence is not None else 9999.0
            # Fall through to display value for text columns
            return self.data(index, Qt.DisplayRole)

        return None


class VariableFilterProxy(QSortFilterProxyModel):
    """Proxy that filters by text search, section, and optional flags."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._active_sections: Set[str] = set()   # empty → show all sections
        self._search_text: str = ""
        self._priority_only: bool = False
        self._hide_solar_conj: bool = False
        self.setSortRole(Qt.UserRole)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)

    def set_active_sections(self, codes: Set[str]) -> None:
        self._active_sections = codes
        self.invalidateFilter()

    def set_search_text(self, text: str) -> None:
        self._search_text = text.strip().lower()
        self.invalidateFilter()

    def set_priority_only(self, on: bool) -> None:
        self._priority_only = on
        self.invalidateFilter()

    def set_hide_solar_conj(self, on: bool) -> None:
        self._hide_solar_conj = on
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model: VariableListModel = self.sourceModel()
        t = model.target_at(source_row)
        if t is None:
            return False

        if self._priority_only and not t.priority:
            return False

        if self._hide_solar_conj and t.solar_conjunction:
            return False

        # Section filter: empty set means "show all"
        if self._active_sections:
            target_codes = {
                SECTION_CODES[s] for s in t.obs_section if s in SECTION_CODES
            }
            if not (self._active_sections & target_codes):
                return False

        # Text search across name, type, and constellation
        if self._search_text:
            haystack = f"{t.star_name} {t.var_type} {t.constellation}".lower()
            if self._search_text not in haystack:
                return False

        return True

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        """Numeric sort for magnitude, RA, Dec, and cadence columns."""
        model: VariableListModel = self.sourceModel()
        tl = model.target_at(left.row())
        tr = model.target_at(right.row())
        if tl is None or tr is None:
            return super().lessThan(left, right)

        col = left.column()
        if col in (0, 3, 4, 6, 7, 9):
            vl = model.data(left, Qt.UserRole)
            vr = model.data(right, Qt.UserRole)
            if vl is not None and vr is not None:
                try:
                    return float(vl) < float(vr)
                except (TypeError, ValueError):
                    pass
        return super().lessThan(left, right)


# ─────────────────────────────────────────────────────────────────────────────
# Targets model
# ─────────────────────────────────────────────────────────────────────────────

class TargetsModel(QAbstractTableModel):
    """Editable model for the observation plan target list."""

    HEADERS = ["Name", "RA (h)", "Dec (°)", "Filter", "Count", "Interval (s)", "Binning"]
    _EDITABLE = {3, 4, 5, 6}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._targets: List[ObservingTarget] = []

    # ── Public API ────────────────────────────────────────────────────────────

    def set_targets(self, targets: List[ObservingTarget]) -> None:
        self.beginResetModel()
        self._targets = list(targets)
        self.endResetModel()

    def get_targets(self) -> List[ObservingTarget]:
        return list(self._targets)

    def add_target(self, ot: ObservingTarget) -> bool:
        """Add a target; returns False if the star is already in the list."""
        for existing in self._targets:
            if existing.aavso.star_name == ot.aavso.star_name:
                return False
        row = len(self._targets)
        self.beginInsertRows(QModelIndex(), row, row)
        self._targets.append(ot)
        self.endInsertRows()
        return True

    def remove_rows_by_index(self, rows: List[int]) -> None:
        for row in sorted(rows, reverse=True):
            if 0 <= row < len(self._targets):
                self.beginRemoveRows(QModelIndex(), row, row)
                self._targets.pop(row)
                self.endRemoveRows()

    def clear(self) -> None:
        self.beginResetModel()
        self._targets.clear()
        self.endResetModel()

    def sort_by_ra(self) -> None:
        self.beginResetModel()
        self._targets.sort(key=lambda t: t.aavso.ra)
        self.endResetModel()

    def move_up(self, row: int) -> None:
        if row <= 0:
            return
        self.beginResetModel()
        self._targets[row - 1], self._targets[row] = (
            self._targets[row], self._targets[row - 1]
        )
        self.endResetModel()

    def move_down(self, row: int) -> None:
        if row >= len(self._targets) - 1:
            return
        self.beginResetModel()
        self._targets[row], self._targets[row + 1] = (
            self._targets[row + 1], self._targets[row]
        )
        self.endResetModel()

    def target_at(self, row: int) -> Optional[ObservingTarget]:
        return self._targets[row] if 0 <= row < len(self._targets) else None

    # ── QAbstractTableModel interface ─────────────────────────────────────────

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._targets)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEADERS[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        f = super().flags(index)
        if index.column() in self._EDITABLE:
            f |= Qt.ItemIsEditable
        return f

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        ot = self._targets[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0:
                return ot.aavso.star_name
            elif col == 1:
                return f"{ot.aavso.ra_hours:.6f}"
            elif col == 2:
                return f"{ot.aavso.dec:+.6f}"
            elif col == 3:
                return ot.script_filters
            elif col == 4:
                return ot.script_counts
            elif col == 5:
                return ot.script_intervals
            elif col == 6:
                return ot.script_binning

        elif role == Qt.ToolTipRole:
            t = ot.aavso
            if col <= 2:
                return (
                    f"{t.var_type}  |  {t.constellation}  |  "
                    f"Max: {t.max_mag} {t.max_mag_band}  "
                    f"Min: {t.min_mag} {t.min_mag_band}"
                )
            return "Click to edit"

        return None

    def setData(self, index: QModelIndex, value, role: int = Qt.EditRole) -> bool:
        if not index.isValid() or role != Qt.EditRole:
            return False
        ot = self._targets[index.row()]
        val = str(value).strip()
        col = index.column()
        if col == 3:
            ot.script_filters = val
        elif col == 4:
            ot.script_counts = val
        elif col == 5:
            ot.script_intervals = val
        elif col == 6:
            ot.script_binning = val
        else:
            return False
        self.dataChanged.emit(index, index, [role])
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Script preview dialog
# ─────────────────────────────────────────────────────────────────────────────

class ScriptPreviewDialog(QDialog):
    """Read-only view of the generated script with a Save button."""

    def __init__(self, script_text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Script Preview")
        self.resize(640, 540)
        self._script_text = script_text
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(self._script_text)
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.Monospace)
        text.setFont(font)
        layout.addWidget(text)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close)
        btns.button(QDialogButtonBox.Save).setText("Save to File…")
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Script", "", "Text Files (*.txt);;All Files (*)"
        )
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self._script_text)
            self.accept()


# ─────────────────────────────────────────────────────────────────────────────
# Import-from-text dialog
# ─────────────────────────────────────────────────────────────────────────────

class ImportTextDialog(QDialog):
    """Paste a tab-separated variable star list and import it into the plan.

    Expected column order (tab-separated, header row optional):
        Name  |  Coords                    |  Type  |  Mag
        Z And |  23 33 39.95 +48 49 05.9  |  ZAND  |  7.7 - 11.3 V

    Coordinates must be sexagesimal J2000: HH MM SS.ss ±DD MM SS.s
    """

    _PLACEHOLDER = (
        "Name\tCoords\tType\tMag\n"
        "Z And\t23 33 39.95 +48 49 05.9\tZAND\t7.7 - 11.3 V\n"
        "SS Cyg\t21 42 42.80 +43 35 09.9\tUGSS\t7.7 - 12.4 V"
    )

    def __init__(
        self,
        default_filters: str,
        default_counts: str,
        default_intervals: str,
        default_binning: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import Variables from Text")
        self.resize(720, 500)
        self._defaults = (default_filters, default_counts, default_intervals, default_binning)
        self._parsed: List[ObservingTarget] = []
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        instr = QLabel(
            "Paste <b>tab-separated</b> data with columns: "
            "<b>Name</b> &nbsp;|&nbsp; <b>Coords</b> &nbsp;|&nbsp; "
            "<b>Type</b> &nbsp;|&nbsp; <b>Mag</b><br>"
            "Coordinates: &nbsp;<code>HH MM SS.ss &nbsp;±DD MM SS.s</code>&nbsp; "
            "(sexagesimal J2000).&nbsp; A header row is skipped automatically."
        )
        instr.setTextFormat(Qt.RichText)
        instr.setWordWrap(True)
        layout.addWidget(instr)

        mono = QFont("Consolas", 9)
        mono.setStyleHint(QFont.Monospace)

        self._text_edit = QTextEdit()
        self._text_edit.setPlaceholderText(self._PLACEHOLDER)
        self._text_edit.setFont(mono)
        layout.addWidget(self._text_edit, 1)

        # Parse button + status label
        parse_row = QHBoxLayout()
        parse_btn = QPushButton("Parse")
        parse_btn.setToolTip("Parse the pasted text and check for errors")
        parse_btn.clicked.connect(self._do_parse)
        parse_row.addWidget(parse_btn)
        self._status_lbl = QLabel("Paste data above and click Parse.")
        self._status_lbl.setTextFormat(Qt.RichText)
        parse_row.addWidget(self._status_lbl, 1)
        layout.addLayout(parse_row)

        # Error/detail output (hidden until needed)
        self._detail_edit = QTextEdit()
        self._detail_edit.setReadOnly(True)
        self._detail_edit.setMaximumHeight(110)
        self._detail_edit.setFont(mono)
        self._detail_edit.setVisible(False)
        layout.addWidget(self._detail_edit)

        # OK / Cancel
        self._add_btn = QPushButton("Add to Plan")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self._add_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    # ── Parsing ───────────────────────────────────────────────────────────────

    def _do_parse(self) -> None:
        targets, errors = self._parse_text(self._text_edit.toPlainText())
        self._parsed = targets

        if targets:
            self._status_lbl.setText(
                f"<span style='color: green;'>&#10003; "
                f"{len(targets)} target(s) ready to add.</span>"
                + (f" &nbsp;<span style='color: #b87820;'>"
                   f"{len(errors)} row(s) skipped.</span>" if errors else "")
            )
            self._add_btn.setText(f"Add {len(targets)} Target(s) to Plan")
            self._add_btn.setEnabled(True)
        else:
            self._status_lbl.setText(
                "<span style='color: red;'>No valid targets found. "
                "Check errors below.</span>"
            )
            self._add_btn.setEnabled(False)

        if errors:
            self._detail_edit.setPlainText("\n".join(errors))
            self._detail_edit.setVisible(True)
        else:
            self._detail_edit.setVisible(False)

        self.adjustSize()

    def get_targets(self) -> List[ObservingTarget]:
        return list(self._parsed)

    # ── Static helpers ────────────────────────────────────────────────────────

    def _parse_text(
        self, text: str
    ) -> tuple:   # (List[ObservingTarget], List[str])
        """Parse all lines and return (targets, error_messages)."""
        import re

        default_filters, default_counts, default_intervals, default_binning = self._defaults
        targets: List[ObservingTarget] = []
        errors: List[str] = []

        for lineno, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line:
                continue

            fields = line.split("\t")

            # Skip header-like rows (first non-empty line with no digits in col 0)
            name = fields[0].strip()
            if name.lower() in ("name", "star", "target", "object", "variable"):
                continue

            if len(fields) < 2:
                errors.append(f"Line {lineno}: too few tab-separated columns — skipped")
                continue

            coord_str = fields[1].strip() if len(fields) > 1 else ""
            type_str  = fields[2].strip() if len(fields) > 2 else ""
            mag_str   = fields[3].strip() if len(fields) > 3 else ""

            try:
                ra_deg, dec_deg = self._parse_coord(coord_str)
            except ValueError as exc:
                errors.append(f"Line {lineno} ({name!r}): {exc}")
                continue

            max_mag, min_mag, band = self._parse_mag(mag_str)

            aavso = AAVSOTarget(
                star_name=name,
                ra=ra_deg,
                dec=dec_deg,
                var_type=type_str,
                max_mag=max_mag,
                max_mag_band=band,
                min_mag=min_mag,
                min_mag_band=band,
            )
            targets.append(
                ObservingTarget(
                    aavso=aavso,
                    script_filters=default_filters,
                    script_counts=default_counts,
                    script_intervals=default_intervals,
                    script_binning=default_binning,
                )
            )

        return targets, errors

    @staticmethod
    def _parse_coord(coord_str: str) -> tuple:   # (ra_deg, dec_deg)
        """Convert sexagesimal 'HH MM SS.ss ±DD MM SS.s' to decimal degrees."""
        import re

        if not coord_str:
            raise ValueError("coordinate field is empty")

        # Collapse any space between a sign character and its digits
        coord_str = re.sub(r'([+-])\s+(\d)', r'\1\2', coord_str)
        parts = coord_str.split()

        if len(parts) < 6:
            raise ValueError(
                f"expected 6 tokens (HH MM SS.ss \u00b1DD MM SS.s), got {len(parts)}: {coord_str!r}"
            )

        try:
            ra_h, ra_m, ra_s = float(parts[0]), float(parts[1]), float(parts[2])
        except ValueError:
            raise ValueError(f"non-numeric RA in {coord_str!r}")

        if not (0.0 <= ra_h < 24.0):
            raise ValueError(f"RA hours out of range ({ra_h}); did you paste degrees instead of hours?")

        ra_deg = (ra_h + ra_m / 60.0 + ra_s / 3600.0) * 15.0

        try:
            dec_raw = parts[3]
            negative = dec_raw.startswith("-")
            dec_d = abs(float(dec_raw))
            dec_m, dec_s = float(parts[4]), float(parts[5])
        except ValueError:
            raise ValueError(f"non-numeric Dec in {coord_str!r}")

        dec_deg = dec_d + dec_m / 60.0 + dec_s / 3600.0
        if negative:
            dec_deg = -dec_deg

        return ra_deg, dec_deg

    @staticmethod
    def _parse_mag(mag_str: str) -> tuple:   # (max_mag, min_mag, band)
        """Parse '7.7 - 11.3 V' \u2192 (max_mag, min_mag, band). Lenient."""
        import re

        if not mag_str:
            return None, None, ""
        m = re.match(r'([\d.]+)\s*(?:-\s*([\d.]+))?\s*([A-Za-z]*)', mag_str.strip())
        if not m:
            return None, None, ""
        max_mag = float(m.group(1))
        min_mag = float(m.group(2)) if m.group(2) else max_mag
        band = m.group(3).strip()
        return max_mag, min_mag, band


# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    """Top-level application window."""

    def __init__(self) -> None:
        super().__init__()
        self.settings = SettingsManager()
        self.exporter = ScriptExporter()
        self._fetch_thread: Optional[FetchTargetsThread] = None

        self.setWindowTitle("VSTarget – AAVSO Variable Star Observation Planner")
        self.resize(1280, 800)

        self._build_menu()
        central = QSplitter(Qt.Horizontal)
        central.addWidget(self._build_variable_panel())
        central.addWidget(self._build_targets_panel())
        central.setStretchFactor(0, 55)
        central.setStretchFactor(1, 45)
        self.setCentralWidget(central)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage(
            f"Ready  |  Telescope: {self.settings.telescope_name}"
        )

    # ── Menu bar ─────────────────────────────────────────────────────────────

    def _build_menu(self) -> None:
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        import_act = QAction("&Import Variables from Text…", self)
        import_act.setShortcut(QKeySequence("Ctrl+I"))
        import_act.setToolTip("Paste a tab-separated variable star list to add to the plan")
        import_act.triggered.connect(self._import_from_text)
        file_menu.addAction(import_act)

        file_menu.addSeparator()
        preview_act = QAction("&Preview Script", self)
        preview_act.setShortcut(QKeySequence("Ctrl+P"))
        preview_act.triggered.connect(self._preview_script)
        file_menu.addAction(preview_act)

        export_act = QAction("&Export Script…", self)
        export_act.setShortcut(QKeySequence("Ctrl+E"))
        export_act.triggered.connect(self._export_script)
        file_menu.addAction(export_act)

        file_menu.addSeparator()
        quit_act = QAction("E&xit", self)
        quit_act.setShortcut(QKeySequence("Ctrl+Q"))
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        edit_menu = mb.addMenu("&Edit")
        settings_act = QAction("&Settings…", self)
        settings_act.setShortcut(QKeySequence("Ctrl+,"))
        settings_act.triggered.connect(self._open_settings)
        edit_menu.addAction(settings_act)

    # ── Left panel: Variable List ─────────────────────────────────────────────

    def _build_variable_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 3, 6)

        # Title + Download button
        title_row = QHBoxLayout()
        lbl = QLabel("AAVSO Variable List")
        font = lbl.font()
        font.setBold(True)
        lbl.setFont(font)
        title_row.addWidget(lbl)
        title_row.addStretch()

        settings_btn = QPushButton("⚙ Settings…")
        settings_btn.setToolTip("Configure API key and telescope location")
        settings_btn.clicked.connect(self._open_settings)
        title_row.addWidget(settings_btn)

        self._download_btn = QPushButton("⬇  Download")
        self._download_btn.setToolTip("Fetch targets from the AAVSO Target Tool API")
        self._download_btn.clicked.connect(self._download_targets)
        title_row.addWidget(self._download_btn)
        layout.addLayout(title_row)

        # ── Section checkboxes ────────────────────────────────────────────────
        section_box = QGroupBox("Sections  (used for download and display filter)")
        section_grid_layout = QVBoxLayout(section_box)
        section_grid_layout.setSpacing(2)

        # "Select all / none" links
        quick_row = QHBoxLayout()
        quick_row.addWidget(QLabel("Select:"))
        all_btn = QPushButton("All")
        all_btn.setFlat(True)
        all_btn.setStyleSheet(
            "QPushButton { color: #0078d4; border: none; padding: 0 4px; }"
            "QPushButton:hover { text-decoration: underline; }"
        )
        all_btn.clicked.connect(lambda: self._set_all_sections(True))
        none_btn = QPushButton("None")
        none_btn.setFlat(True)
        none_btn.setStyleSheet(all_btn.styleSheet())
        none_btn.clicked.connect(lambda: self._set_all_sections(False))
        quick_row.addWidget(all_btn)
        quick_row.addWidget(none_btn)
        quick_row.addStretch()
        section_grid_layout.addLayout(quick_row)

        # Two-column checkbox grid
        from PySide6.QtWidgets import QGridLayout as _QGL
        grid = _QGL()
        grid.setSpacing(2)
        saved = set(self.settings.selected_sections)
        self._section_cbs: dict[str, QCheckBox] = {}
        items = list(SECTION_CODES.items())
        for i, (display_name, code) in enumerate(items):
            cb = QCheckBox(display_name)
            cb.setChecked(code in saved)
            cb.stateChanged.connect(self._on_section_changed)
            self._section_cbs[code] = cb
            grid.addWidget(cb, i // 2, i % 2)
        section_grid_layout.addLayout(grid)
        layout.addWidget(section_box)

        # ── Additional display filters ────────────────────────────────────────
        filter_row = QHBoxLayout()
        self._observable_cb = QCheckBox("Observable only (next night)")
        self._observable_cb.setToolTip(
            "When downloading, the API will only return targets visible from the "
            "configured telescope location during the next nighttime period."
        )
        filter_row.addWidget(self._observable_cb)
        self._priority_only_cb = QCheckBox("Priority only")
        self._priority_only_cb.stateChanged.connect(
            lambda s: self._var_proxy.set_priority_only(s == Qt.Checked)
        )
        filter_row.addWidget(self._priority_only_cb)
        self._hide_solar_cb = QCheckBox("Hide solar conj.")
        self._hide_solar_cb.stateChanged.connect(
            lambda s: self._var_proxy.set_hide_solar_conj(s == Qt.Checked)
        )
        filter_row.addWidget(self._hide_solar_cb)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        # ── Text search ───────────────────────────────────────────────────────
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Filter by name, type, or constellation…")
        self._search_edit.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self._search_edit)
        layout.addLayout(search_row)

        # ── Variable list table ───────────────────────────────────────────────
        self._var_model = VariableListModel()
        self._var_proxy = VariableFilterProxy()
        self._var_proxy.setSourceModel(self._var_model)

        # Initialise section filter to match saved checkboxes
        initial_sections = {c for c, cb in self._section_cbs.items() if cb.isChecked()}
        self._var_proxy.set_active_sections(initial_sections)

        self._var_table = QTableView()
        self._var_table.setModel(self._var_proxy)
        self._var_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._var_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._var_table.setSortingEnabled(True)
        self._var_table.setAlternatingRowColors(True)
        self._var_table.verticalHeader().setVisible(False)
        self._var_table.horizontalHeader().setStretchLastSection(True)
        self._var_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._var_table.doubleClicked.connect(self._add_selected_targets)
        self._var_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._var_table.customContextMenuRequested.connect(self._show_variable_context_menu)
        self._var_table.sortByColumn(6, Qt.AscendingOrder)

        hh = self._var_table.horizontalHeader()
        for col, width in [(0, 24), (1, 130), (2, 90), (3, 75), (4, 75),
                           (5, 70), (6, 80), (7, 80), (8, 46)]:
            self._var_table.setColumnWidth(col, width)
        hh.setSectionResizeMode(1, QHeaderView.Interactive)
        layout.addWidget(self._var_table)

        # ── Add buttons row ───────────────────────────────────────────────────
        add_row = QHBoxLayout()
        self._count_label = QLabel("No targets downloaded")
        self._count_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        add_row.addWidget(self._count_label)
        add_all_btn = QPushButton("Add All Visible →")
        add_all_btn.setToolTip("Add every currently visible target to the observation plan")
        add_all_btn.clicked.connect(self._add_all_visible)
        add_sel_btn = QPushButton("Add Selected →")
        add_sel_btn.setToolTip("Add the selected rows to the observation plan (or double-click)")
        add_sel_btn.clicked.connect(self._add_selected_targets)
        add_row.addWidget(add_all_btn)
        add_row.addWidget(add_sel_btn)
        layout.addLayout(add_row)

        return panel

    # ── Right panel: Targets ──────────────────────────────────────────────────

    def _build_targets_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(3, 6, 6, 6)

        # Title
        title_lbl = QLabel("Observation Targets  (exported sorted by RA)")
        font = title_lbl.font()
        font.setBold(True)
        title_lbl.setFont(font)
        layout.addWidget(title_lbl)

        # Toolbar row
        btn_row = QHBoxLayout()
        move_up_btn = QPushButton("▲")
        move_up_btn.setFixedWidth(34)
        move_up_btn.setToolTip("Move selected target up")
        move_up_btn.clicked.connect(self._move_target_up)

        move_dn_btn = QPushButton("▼")
        move_dn_btn.setFixedWidth(34)
        move_dn_btn.setToolTip("Move selected target down")
        move_dn_btn.clicked.connect(self._move_target_down)

        remove_btn = QPushButton("✕ Remove")
        remove_btn.setToolTip("Remove selected targets from the plan")
        remove_btn.clicked.connect(self._remove_selected_targets)

        clear_btn = QPushButton("🗑 Clear All")
        clear_btn.setToolTip("Remove all targets from the plan")
        clear_btn.clicked.connect(self._clear_targets)

        sort_btn = QPushButton("⇅ Sort by RA")
        sort_btn.setToolTip("Sort the plan by Right Ascension (ascending)")
        sort_btn.clicked.connect(self._sort_targets_by_ra)

        for w in (move_up_btn, move_dn_btn, remove_btn, clear_btn):
            btn_row.addWidget(w)
        btn_row.addStretch()
        btn_row.addWidget(sort_btn)
        layout.addLayout(btn_row)

        # Target table
        self._tgt_model = TargetsModel()
        self._tgt_table = QTableView()
        self._tgt_table.setModel(self._tgt_model)
        self._tgt_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tgt_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._tgt_table.setAlternatingRowColors(True)
        self._tgt_table.verticalHeader().setVisible(False)
        self._tgt_table.horizontalHeader().setStretchLastSection(True)
        for col, width in [(0, 130), (1, 90), (2, 90), (3, 90), (4, 60), (5, 80)]:
            self._tgt_table.setColumnWidth(col, width)
        layout.addWidget(self._tgt_table)

        # Script options group
        script_box = QGroupBox("Script Options")
        script_vbox = QVBoxLayout(script_box)

        # Global directives row
        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Directives:"))
        self._chk_defocus = QCheckBox("#defocus")
        self._chk_defocus.setChecked(self.settings.directive_defocus)
        self._chk_defocus.setToolTip("Slightly defocus images (useful for photometry)")
        self._chk_vphot = QCheckBox("#vphot")
        self._chk_vphot.setChecked(self.settings.directive_vphot)
        self._chk_vphot.setToolTip("Send images to VPHOT for photometry (requires AAVSO membership)")
        self._chk_platesolve = QCheckBox("#platesolve")
        self._chk_platesolve.setChecked(self.settings.directive_platesolve)
        self._chk_platesolve.setToolTip("Force plate-solve on each image")
        self._chk_filteroffsets = QCheckBox("#filteroffsets")
        self._chk_filteroffsets.setChecked(self.settings.directive_filteroffsets)
        self._chk_filteroffsets.setToolTip("Focus using filter offsets instead of re-focusing on each filter change")
        for cb in (self._chk_defocus, self._chk_vphot, self._chk_platesolve, self._chk_filteroffsets):
            dir_row.addWidget(cb)
        dir_row.addStretch()
        script_vbox.addLayout(dir_row)

        # Default parameters for new targets
        def_box = QGroupBox("New Target Defaults")
        from PySide6.QtWidgets import QGridLayout as _GL2
        def_grid = _GL2()
        def_grid.setHorizontalSpacing(6)
        def_grid.setVerticalSpacing(4)

        self._def_filter = QLineEdit(self.settings.default_filters)
        self._def_count = QLineEdit(self.settings.default_counts)
        self._def_interval = QLineEdit(self.settings.default_intervals)
        self._def_binning = QLineEdit(self.settings.default_binning)
        for le in (self._def_filter, self._def_count, self._def_interval, self._def_binning):
            le.setMaximumWidth(120)

        def_grid.addWidget(QLabel("Filter:"), 0, 0)
        def_grid.addWidget(self._def_filter, 0, 1)
        def_grid.addWidget(QLabel("Count:"), 0, 2)
        def_grid.addWidget(self._def_count, 0, 3)
        def_grid.addWidget(QLabel("Interval (s):"), 1, 0)
        def_grid.addWidget(self._def_interval, 1, 1)
        def_grid.addWidget(QLabel("Binning:"), 1, 2)
        def_grid.addWidget(self._def_binning, 1, 3)

        apply_btn = QPushButton("Apply to All Targets in Plan")
        apply_btn.setToolTip("Overwrite every target's script parameters with these defaults")
        apply_btn.clicked.connect(self._apply_defaults_to_all)
        def_grid.addWidget(apply_btn, 2, 0, 1, 4)

        def_box.setLayout(def_grid)
        script_vbox.addWidget(def_box)

        # Export buttons
        export_row = QHBoxLayout()
        export_row.addStretch()
        preview_btn = QPushButton("📄 Preview Script")
        preview_btn.setToolTip("View the generated script before saving")
        preview_btn.clicked.connect(self._preview_script)
        export_btn = QPushButton("💾 Export Script…")
        export_btn.setToolTip("Save the script as a .txt file for upload to iTelescope")
        export_btn.clicked.connect(self._export_script)
        export_row.addWidget(preview_btn)
        export_row.addWidget(export_btn)
        script_vbox.addLayout(export_row)

        layout.addWidget(script_box)
        return panel

    # ── Settings ──────────────────────────────────────────────────────────────

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec():
            self._def_filter.setText(self.settings.default_filters)
            self._def_count.setText(self.settings.default_counts)
            self._def_interval.setText(self.settings.default_intervals)
            self._def_binning.setText(self.settings.default_binning)
            self._status.showMessage(
                f"Settings saved  |  Telescope: {self.settings.telescope_name}"
            )

    # ── Download ──────────────────────────────────────────────────────────────

    def _download_targets(self) -> None:
        if not self.settings.api_key:
            QMessageBox.warning(
                self, "No API Key",
                "Please configure your AAVSO API key in Settings before downloading."
            )
            self._open_settings()
            return

        sections = [c for c, cb in self._section_cbs.items() if cb.isChecked()]
        if not sections:
            QMessageBox.information(
                self, "No Sections Selected",
                "Please check at least one section to download."
            )
            return

        self._download_btn.setEnabled(False)
        self._download_btn.setText("⬇  Downloading…")
        self._status.showMessage("Connecting to AAVSO Target Tool API…")

        self._fetch_thread = FetchTargetsThread(
            api_key=self.settings.api_key,
            sections=sections,
            observable=self._observable_cb.isChecked(),
            latitude=self.settings.latitude,
            longitude=self.settings.longitude,
            target_altitude=self.settings.target_altitude,
            sun_altitude=self.settings.sun_altitude,
            parent=self,
        )
        self._fetch_thread.finished.connect(self._on_targets_downloaded)
        self._fetch_thread.error.connect(self._on_download_error)
        self._fetch_thread.status.connect(self._status.showMessage)
        self._fetch_thread.start()

        # Save the chosen sections
        self.settings.selected_sections = sections

    def _on_targets_downloaded(self, targets: list) -> None:
        self._var_model.set_targets(targets)
        self._download_btn.setEnabled(True)
        self._download_btn.setText("⬇  Download")
        self._update_count_label()
        self._status.showMessage(
            f"Downloaded {len(targets)} targets  |  "
            f"Showing {self._var_proxy.rowCount()} after filters"
        )

    def _on_download_error(self, msg: str) -> None:
        self._download_btn.setEnabled(True)
        self._download_btn.setText("⬇  Download")
        self._status.showMessage("Download failed")
        QMessageBox.critical(self, "Download Error", msg)

    # ── Section / filter callbacks ────────────────────────────────────────────

    def _on_section_changed(self) -> None:
        active = {c for c, cb in self._section_cbs.items() if cb.isChecked()}
        self._var_proxy.set_active_sections(active)
        self._update_count_label()

    def _set_all_sections(self, checked: bool) -> None:
        for cb in self._section_cbs.values():
            cb.blockSignals(True)
            cb.setChecked(checked)
            cb.blockSignals(False)
        active = set(self._section_cbs.keys()) if checked else set()
        self._var_proxy.set_active_sections(active)
        self._update_count_label()

    def _on_search_changed(self, text: str) -> None:
        self._var_proxy.set_search_text(text)
        self._update_count_label()

    def _update_count_label(self) -> None:
        total = self._var_model.rowCount()
        shown = self._var_proxy.rowCount()
        if total == 0:
            self._count_label.setText("No targets downloaded")
        elif total == shown:
            self._count_label.setText(f"{total} targets")
        else:
            self._count_label.setText(f"{shown} of {total} shown")

    # ── Adding targets ────────────────────────────────────────────────────────

    def _make_observing_target(self, t: AAVSOTarget) -> ObservingTarget:
        return ObservingTarget(
            aavso=t,
            script_filters=self._def_filter.text().strip() or self.settings.default_filters,
            script_counts=self._def_count.text().strip() or self.settings.default_counts,
            script_intervals=self._def_interval.text().strip() or self.settings.default_intervals,
            script_binning=self._def_binning.text().strip() or self.settings.default_binning,
        )

    def _add_selected_targets(self) -> None:
        indexes = self._var_table.selectionModel().selectedRows()
        added = 0
        for proxy_idx in indexes:
            src_idx = self._var_proxy.mapToSource(proxy_idx)
            t = self._var_model.target_at(src_idx.row())
            if t and self._tgt_model.add_target(self._make_observing_target(t)):
                added += 1
        if added:
            self._status.showMessage(
                f"Added {added} target(s)  |  {self._tgt_model.rowCount()} in plan"
            )

    def _add_all_visible(self) -> None:
        added = 0
        for row in range(self._var_proxy.rowCount()):
            src_idx = self._var_proxy.mapToSource(self._var_proxy.index(row, 0))
            t = self._var_model.target_at(src_idx.row())
            if t and self._tgt_model.add_target(self._make_observing_target(t)):
                added += 1
        self._status.showMessage(
            f"Added {added} target(s)  |  {self._tgt_model.rowCount()} in plan"
        )

    # ── Managing targets ──────────────────────────────────────────────────────

    def _remove_selected_targets(self) -> None:
        rows = sorted(
            {idx.row() for idx in self._tgt_table.selectionModel().selectedRows()},
            reverse=True,
        )
        self._tgt_model.remove_rows_by_index(rows)
        self._status.showMessage(f"{self._tgt_model.rowCount()} targets in plan")

    def _clear_targets(self) -> None:
        if self._tgt_model.rowCount() == 0:
            return
        reply = QMessageBox.question(
            self, "Clear Plan",
            "Remove all targets from the observation plan?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._tgt_model.clear()
            self._status.showMessage("Observation plan cleared")

    def _sort_targets_by_ra(self) -> None:
        self._tgt_model.sort_by_ra()
        self._status.showMessage("Targets sorted by RA (ascending)")

    def _move_target_up(self) -> None:
        rows = [idx.row() for idx in self._tgt_table.selectionModel().selectedRows()]
        if len(rows) == 1:
            row = rows[0]
            self._tgt_model.move_up(row)
            self._tgt_table.selectRow(max(0, row - 1))

    def _move_target_down(self) -> None:
        rows = [idx.row() for idx in self._tgt_table.selectionModel().selectedRows()]
        if len(rows) == 1:
            row = rows[0]
            n = self._tgt_model.rowCount()
            self._tgt_model.move_down(row)
            self._tgt_table.selectRow(min(row + 1, n - 1))

    def _apply_defaults_to_all(self) -> None:
        targets = self._tgt_model.get_targets()
        if not targets:
            return
        for ot in targets:
            ot.script_filters = self._def_filter.text().strip()
            ot.script_counts = self._def_count.text().strip()
            ot.script_intervals = self._def_interval.text().strip()
            ot.script_binning = self._def_binning.text().strip()
        self._tgt_model.set_targets(targets)
        self._status.showMessage(
            f"Applied defaults to all {len(targets)} targets"
        )

    # ── Script generation / export ────────────────────────────────────────────

    def _check_ready_to_export(self) -> bool:
        """Return True if there are targets and validation passes (with warning)."""
        targets = self._tgt_model.get_targets()
        if not targets:
            QMessageBox.information(
                self, "No Targets",
                "Add targets to the observation plan before exporting."
            )
            return False
        warnings = self.exporter.validate(targets)
        if warnings:
            msg = (
                "The following validation issues were found:\n\n"
                + "\n".join(f"• {w}" for w in warnings)
                + "\n\nProceed anyway?"
            )
            reply = QMessageBox.warning(
                self, "Validation Warnings", msg,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            return reply == QMessageBox.Yes
        return True

    def _build_script(self) -> str:
        return self.exporter.generate(
            targets=self._tgt_model.get_targets(),
            defocus=self._chk_defocus.isChecked(),
            vphot=self._chk_vphot.isChecked(),
            platesolve=self._chk_platesolve.isChecked(),
            filteroffsets=self._chk_filteroffsets.isChecked(),
        )

    def _preview_script(self) -> None:
        if not self._check_ready_to_export():
            return
        dlg = ScriptPreviewDialog(self._build_script(), self)
        dlg.exec()

    def _export_script(self) -> None:
        if not self._check_ready_to_export():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export iTelescope Script", "",
            "Text Files (*.txt);;All Files (*)",
        )
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self._build_script())
            self._status.showMessage(f"Script saved: {path}")

    # ── Import from text ──────────────────────────────────────────────────────

    def _import_from_text(self) -> None:
        dlg = ImportTextDialog(
            default_filters=self._def_filter.text().strip() or self.settings.default_filters,
            default_counts=self._def_count.text().strip() or self.settings.default_counts,
            default_intervals=self._def_interval.text().strip() or self.settings.default_intervals,
            default_binning=self._def_binning.text().strip() or self.settings.default_binning,
            parent=self,
        )
        if dlg.exec():
            targets = dlg.get_targets()
            added = skipped = 0
            for ot in targets:
                if self._tgt_model.add_target(ot):
                    added += 1
                else:
                    skipped += 1
            parts = [f"Imported {added} target(s)"]
            if skipped:
                parts.append(f"{skipped} already in plan (skipped)")
            parts.append(f"{self._tgt_model.rowCount()} total in plan")
            self._status.showMessage("  |  ".join(parts))

    # ── Variable list context menu ──────────────────────────────────────────

    def _show_variable_context_menu(self, pos) -> None:
        import re
        import urllib.parse

        proxy_idx = self._var_table.indexAt(pos)
        if not proxy_idx.isValid():
            return

        src_idx = self._var_proxy.mapToSource(proxy_idx)
        t = self._var_model.target_at(src_idx.row())
        if t is None:
            return

        menu = QMenu(self)

        # ── Add to plan ───────────────────────────────────────────────────────
        add_act = menu.addAction(f"Add '{t.star_name}' to Plan")
        add_act.triggered.connect(self._add_selected_targets)
        menu.addSeparator()

        # ── AAVSO VSX (Variable Star Index) ──────────────────────────────────
        encoded_name = urllib.parse.quote(t.star_name)
        vsx_url = QUrl(
            f"https://www.aavso.org/vsx/index.php?view=results.get&ident={encoded_name}"
        )
        vsx_act = menu.addAction("Open in AAVSO VSX…")
        vsx_act.triggered.connect(lambda: QDesktopServices.openUrl(vsx_url))

        # ── Recent observations (WebObs) ──────────────────────────────────────
        webobs_url = QUrl(
            f"https://www.aavso.org/apps/webobs/results/?star={encoded_name}"
        )
        webobs_act = menu.addAction("Open Recent Observations (WebObs)…")
        webobs_act.triggered.connect(lambda: QDesktopServices.openUrl(webobs_url))

        # ── Campaign / Alert Notice links from other_info ─────────────────────
        # other_info format: [[Description https://url]] (one or more links)
        if t.other_info:
            notice_links = re.findall(
                r'\[\[([^\]]*?)\s+(https?://\S+?)\]\]', t.other_info
            )
            if notice_links:
                menu.addSeparator()
                for description, url in notice_links:
                    label = description.strip() or "Campaign Notice"
                    act = menu.addAction(f"Open: {label}…")
                    captured_url = QUrl(url)
                    act.triggered.connect(
                        lambda checked=False, u=captured_url: QDesktopServices.openUrl(u)
                    )

        menu.exec(self._var_table.viewport().mapToGlobal(pos))

    # ── Window close ─────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # noqa: N802
        # Persist UI state
        self.settings.directive_defocus = self._chk_defocus.isChecked()
        self.settings.directive_vphot = self._chk_vphot.isChecked()
        self.settings.directive_platesolve = self._chk_platesolve.isChecked()
        self.settings.directive_filteroffsets = self._chk_filteroffsets.isChecked()
        self.settings.default_filters = self._def_filter.text().strip()
        self.settings.default_counts = self._def_count.text().strip()
        self.settings.default_intervals = self._def_interval.text().strip()
        self.settings.default_binning = self._def_binning.text().strip()
        self.settings.selected_sections = [
            c for c, cb in self._section_cbs.items() if cb.isChecked()
        ]
        self.settings.sync()
        event.accept()
