"""Main application window: Variable List, Targets panel, and script export."""
from __future__ import annotations

import os
import re

from PySide6.QtWidgets import (
    QAbstractItemView,
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
    Qt,
    QUrl,
)
from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QFont, QKeySequence, QShortcut

from aavso_client import FetchTargetsThread
from database import Database
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
        self._data: list[AAVSOTarget] = []

    # ── Public API ────────────────────────────────────────────────────────────

    def set_targets(self, targets: list[AAVSOTarget]) -> None:
        self.beginResetModel()
        self._data = targets
        self.endResetModel()

    def target_at(self, row: int) -> AAVSOTarget | None:
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
            parts: list[str] = [f"<b>{t.star_name}</b> ({t.var_type})"]
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
        self._active_sections: set[str] = set()   # empty → show all sections
        self._search_text: str = ""
        self._priority_only: bool = False
        self._hide_solar_conj: bool = False
        self.setSortRole(Qt.UserRole)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)

    def set_active_sections(self, codes: set[str]) -> None:
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

    HEADERS = ["Name", "RA (h)", "Dec (°)", "Const.", "Filter ✎", "Count ✎", "Interval (s) ✎", "Binning ✎"]
    _EDITABLE = {4, 5, 6, 7}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._targets: list[ObservingTarget] = []

    # ── Public API ────────────────────────────────────────────────────────────

    def set_targets(self, targets: list[ObservingTarget]) -> None:
        self.beginResetModel()
        self._targets = list(targets)
        self.endResetModel()

    def get_targets(self) -> list[ObservingTarget]:
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

    def remove_rows_by_index(self, rows: list[int]) -> None:
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

    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder) -> None:
        """Sort targets in-place by column (physical reorder; updates export order)."""
        reverse = order == Qt.DescendingOrder
        key_funcs = {
            0: lambda t: t.aavso.star_name.lower(),
            1: lambda t: t.aavso.ra,
            2: lambda t: t.aavso.dec,
            3: lambda t: t.aavso.constellation.lower(),
            4: lambda t: t.script_filters.lower(),
            5: lambda t: t.script_counts.lower(),
            6: lambda t: t.script_intervals.lower(),
            7: lambda t: t.script_binning.lower(),
        }
        key = key_funcs.get(column)
        if key is None:
            return
        self.beginResetModel()
        self._targets.sort(key=key, reverse=reverse)
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

    def target_at(self, row: int) -> ObservingTarget | None:
        return self._targets[row] if 0 <= row < len(self._targets) else None

    # ── QAbstractTableModel interface ─────────────────────────────────────────

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._targets)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal:
            if role == Qt.DisplayRole:
                return self.HEADERS[section]
            if role == Qt.ToolTipRole and section in self._EDITABLE:
                return "Double-click or press F2 to edit"
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
                return ot.aavso.constellation
            elif col == 4:
                return ot.script_filters
            elif col == 5:
                return ot.script_counts
            elif col == 6:
                return ot.script_intervals
            elif col == 7:
                return ot.script_binning

        elif role == Qt.BackgroundRole:
            if col in self._EDITABLE:
                return QBrush(QColor("#fdfaed"))  # soft cream tint for editable cells

        elif role == Qt.ToolTipRole:
            t = ot.aavso
            if col <= 3:
                return (
                    f"{t.var_type}  |  "
                    f"Max: {t.max_mag} {t.max_mag_band}  "
                    f"Min: {t.min_mag} {t.min_mag_band}"
                )
            return "Double-click or press F2 to edit"

        return None

    def setData(self, index: QModelIndex, value, role: int = Qt.EditRole) -> bool:
        if not index.isValid() or role != Qt.EditRole:
            return False
        ot = self._targets[index.row()]
        val = str(value).strip()
        col = index.column()
        if col == 4:
            ot.script_filters = val
        elif col == 5:
            ot.script_counts = val
        elif col == 6:
            ot.script_intervals = val
        elif col == 7:
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
# Target file parser (module-level helpers)
# ─────────────────────────────────────────────────────────────────────────────

def _split_fields(line: str) -> list[str]:
    """Split a line into [Name, Coords, Type, Mag] using the best available strategy.

    Tries in order:
    1. Tab characters  (true TSV / Excel copy)
    2. Two-or-more consecutive spaces  (most web-page copies)
    3. Coord-pattern regex  (single-space separators or unusual formats)
    """
    # Strategy 1 – tabs
    fields = line.split("\t")
    if len(fields) >= 2:
        return [f.strip() for f in fields]

    # Strategy 2 – 2+ spaces
    fields = re.split(r"  +", line)
    if len(fields) >= 2:
        return [f.strip() for f in fields]

    # Strategy 3 – detect the sexagesimal coordinate block and split around it
    m = re.match(
        r"^(.+?)\s+"
        r"(\d{1,2} \d{2} \d{2}[.,]\d+\s+[+\-]\d{1,2} \d{2} \d{2}[.,]?\d*)"
        r"\s+(\S+)"
        r"\s+(.+)$",
        line,
    )
    if m:
        return [m.group(1).strip(), m.group(2).strip(),
                m.group(3).strip(), m.group(4).strip()]

    return [line]


def _parse_coord(coord_str: str) -> tuple[float, float]:
    """Convert sexagesimal 'HH MM SS.ss ±DD MM SS.s' to (ra_deg, dec_deg)."""
    if not coord_str:
        raise ValueError("coordinate field is empty")

    # Collapse any space between a sign character and its digits
    coord_str = re.sub(r'([+-])\s+(\d)', r'\1\2', coord_str)
    parts = coord_str.split()

    if len(parts) < 6:
        raise ValueError(
            f"expected 6 tokens (HH MM SS.ss \u00b1DD MM SS.s), "
            f"got {len(parts)}: {coord_str!r}"
        )

    try:
        ra_h, ra_m, ra_s = float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError:
        raise ValueError(f"non-numeric RA in {coord_str!r}") from None

    if not (0.0 <= ra_h < 24.0):
        raise ValueError(
            f"RA hours out of range ({ra_h}); "
            "did you paste degrees instead of hours?"
        )

    ra_deg = (ra_h + ra_m / 60.0 + ra_s / 3600.0) * 15.0

    try:
        dec_raw = parts[3]
        negative = dec_raw.startswith("-")
        dec_d = abs(float(dec_raw))
        dec_m, dec_s = float(parts[4]), float(parts[5])
    except ValueError:
        raise ValueError(f"non-numeric Dec in {coord_str!r}") from None

    dec_deg = dec_d + dec_m / 60.0 + dec_s / 3600.0
    if negative:
        dec_deg = -dec_deg

    return ra_deg, dec_deg


def _parse_mag(mag_str: str) -> tuple[float | None, float | None, str]:
    """Parse '7.7 - 11.3 V' → (max_mag, min_mag, band).  Lenient."""
    if not mag_str:
        return None, None, ""
    m = re.match(r'([\d.]+)\s*(?:-\s*([\d.]+))?\s*([A-Za-z]*)', mag_str.strip())
    if not m:
        return None, None, ""
    max_mag = float(m.group(1))
    min_mag = float(m.group(2)) if m.group(2) else max_mag
    band = m.group(3).strip()
    return max_mag, min_mag, band


def _parse_target_text(
    text: str,
    defaults: tuple[str, str, str, str],
) -> tuple[list[ObservingTarget], list[str]]:
    """Parse a tab/space-separated text block into ObservingTargets.

    Returns ``(targets, error_messages)``.
    """
    default_filters, default_counts, default_intervals, default_binning = defaults
    targets: list[ObservingTarget] = []
    errors: list[str] = []

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue

        fields = _split_fields(line)
        name = fields[0].strip()

        # Skip header rows
        if name.lower() in ("name", "star", "target", "object", "variable"):
            continue

        if len(fields) < 2:
            errors.append(f"Line {lineno}: could not identify columns — skipped")
            continue

        coord_str = fields[1].strip() if len(fields) > 1 else ""
        type_str  = fields[2].strip() if len(fields) > 2 else ""
        mag_str   = fields[3].strip() if len(fields) > 3 else ""

        # The name is "<designation> <Const>" (e.g. "Z And", "V0603 Aql").
        # Split off the last word as the constellation abbreviation.
        name_parts = name.rsplit(None, 1)
        constellation = name_parts[-1] if len(name_parts) > 1 else ""

        try:
            ra_deg, dec_deg = _parse_coord(coord_str)
        except ValueError as exc:
            errors.append(f"Line {lineno} ({name!r}): {exc}")
            continue

        max_mag, min_mag, band = _parse_mag(mag_str)

        aavso = AAVSOTarget(
            star_name=name,
            ra=ra_deg,
            dec=dec_deg,
            var_type=type_str,
            max_mag=max_mag,
            max_mag_band=band,
            min_mag=min_mag,
            min_mag_band=band,
            constellation=constellation,
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


# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    """Top-level application window."""

    def __init__(self) -> None:
        super().__init__()
        self.settings = SettingsManager()
        self.exporter = ScriptExporter()
        self._db = Database()
        self._fetch_thread: FetchTargetsThread | None = None
        self._loading_plan = False   # suppress auto-save during initial load
        self._images_panel = None    # held open as modeless dialog

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

        # Restore plan saved in previous session
        self._loading_plan = True
        saved = self._db.load_plan()
        if saved:
            self._tgt_model.set_targets(saved)
        self._loading_plan = False

        # Auto-save on every change to the targets model
        self._tgt_model.dataChanged.connect(self._autosave_plan)
        self._tgt_model.rowsInserted.connect(self._autosave_plan)
        self._tgt_model.rowsRemoved.connect(self._autosave_plan)
        self._tgt_model.modelReset.connect(self._autosave_plan)

        # Summary counter – incremental updates where possible.
        # rowsAboutToBeRemoved fires while the rows are still in the model,
        # so we can subtract their exposure before they disappear.
        self._summary_count: int = 0
        self._summary_sec: float = 0.0
        self._tgt_model.rowsAboutToBeRemoved.connect(self._on_targets_about_to_remove)
        self._tgt_model.rowsInserted.connect(self._on_targets_inserted)
        self._tgt_model.dataChanged.connect(self._recalc_target_summary)
        self._tgt_model.modelReset.connect(self._recalc_target_summary)
        self._recalc_target_summary()  # initial value (includes restored targets)

        # Persistent DB path indicator in the status bar
        db_lbl = QLabel()
        db_lbl.setText("✓ DB")
        db_lbl.setToolTip(f"Plan database: {self._db.path}")
        db_lbl.setStyleSheet("color: gray; font-size: 11px; padding: 0 6px;")
        self._status.addPermanentWidget(db_lbl)

        # Working directory indicator
        self._workdir_lbl = QLabel()
        self._workdir_lbl.setStyleSheet("color: gray; font-size: 11px; padding: 0 6px;")
        self._status.addPermanentWidget(self._workdir_lbl)
        self._refresh_workdir_label()

        msg = f"Ready  |  Telescope: {self.settings.telescope_name}"
        if saved:
            msg += f"  |  {len(saved)} target(s) restored from previous session"
        self._status.showMessage(msg)

    # ── Menu bar ─────────────────────────────────────────────────────────────

    def _build_menu(self) -> None:
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        import_act = QAction("&Import from File…", self)
        import_act.setShortcut(QKeySequence("Ctrl+I"))
        import_act.setToolTip("Import variable star targets from a .txt or .tsv file")
        import_act.triggered.connect(self._import_from_file)
        file_menu.addAction(import_act)

        dl_act = QAction("&Download FITS Images…", self)
        dl_act.setShortcut(QKeySequence("Ctrl+D"))
        dl_act.setToolTip("Download calibrated FITS images from the iTelescope SFTP server")
        dl_act.triggered.connect(self._open_download_dialog)
        file_menu.addAction(dl_act)

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

        analysis_menu = mb.addMenu("&Analysis")
        workdir_act = QAction("&Select Working Directory…", self)
        workdir_act.setShortcut(QKeySequence("Ctrl+W"))
        workdir_act.setToolTip(
            "Choose a folder of FITS images to use as the working directory for analysis"
        )
        workdir_act.triggered.connect(self._select_working_directory)
        analysis_menu.addAction(workdir_act)

        clear_workdir_act = QAction("Clear Working Directory", self)
        clear_workdir_act.triggered.connect(self._clear_working_directory)
        analysis_menu.addAction(clear_workdir_act)

        analysis_menu.addSeparator()
        load_images_act = QAction("&Load Images…", self)
        load_images_act.setShortcut(QKeySequence("Ctrl+L"))
        load_images_act.setToolTip(
            "Show a list of FITS images in the working directory"
        )
        load_images_act.triggered.connect(self._open_images_panel)
        analysis_menu.addAction(load_images_act)

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

        for w in (move_up_btn, move_dn_btn, remove_btn, clear_btn):
            btn_row.addWidget(w)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Target table
        self._tgt_model = TargetsModel()
        self._tgt_table = QTableView()
        self._tgt_table.setModel(self._tgt_model)
        self._tgt_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tgt_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._tgt_table.setAlternatingRowColors(True)
        self._tgt_table.verticalHeader().setVisible(False)
        hh = self._tgt_table.horizontalHeader()
        hh.setStretchLastSection(True)
        hh.setSectionsClickable(True)
        hh.setSortIndicatorShown(True)
        hh.sectionClicked.connect(self._on_target_header_clicked)
        # Track sort state explicitly so the toggle is not confused by the
        # header's default indicator (which defaults to col 0 / Ascending).
        self._tgt_sort_col: int = -1
        self._tgt_sort_order: Qt.SortOrder = Qt.AscendingOrder
        # Allow editing via double-click, F2, or just starting to type
        self._tgt_table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.AnyKeyPressed
        )
        self._tgt_table.doubleClicked.connect(self._on_target_double_clicked)
        # Delete key removes selected rows; WidgetShortcut ensures it does
        # not fire while a cell editor (QLineEdit) has focus.
        _del_sc = QShortcut(QKeySequence(Qt.Key_Delete), self._tgt_table)
        _del_sc.setContext(Qt.WidgetShortcut)
        _del_sc.activated.connect(self._remove_selected_targets)
        for col, width in [(0, 130), (1, 80), (2, 80), (3, 46), (4, 100), (5, 65), (6, 85)]:
            self._tgt_table.setColumnWidth(col, width)
        layout.addWidget(self._tgt_table)

        hint_lbl = QLabel(
            "\u270e  Click a row to select, then double-click or press F2 / type to edit "
            "Filter\u2009\u00b7\u2009Count\u2009\u00b7\u2009Interval\u2009\u00b7\u2009Binning"
        )
        hint_lbl.setStyleSheet("color: gray; font-size: 11px; padding: 1px 0;")
        layout.addWidget(hint_lbl)

        self._tgt_summary_lbl = QLabel("0 targets  │  Total exposure: —")
        self._tgt_summary_lbl.setStyleSheet("font-size: 12px; padding: 2px 0;")
        layout.addWidget(self._tgt_summary_lbl)

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

        suggest_btn = QPushButton("📏 Suggest Exposures from Calibration")
        suggest_btn.setToolTip(
            "Size each target's Interval for its faint end (min mag) using the\n"
            "exposure calibration measured in the Images panel, capped so\n"
            "comparison stars never saturate"
        )
        suggest_btn.clicked.connect(self._suggest_exposures)
        def_grid.addWidget(suggest_btn, 3, 0, 1, 4)

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

    def _on_target_double_clicked(self, index: QModelIndex) -> None:
        """When a non-editable cell is double-clicked, redirect to the Filter column."""
        if not (index.flags() & Qt.ItemIsEditable):
            filter_col = min(TargetsModel._EDITABLE)   # column 3 = Filter
            editable_idx = self._tgt_model.index(index.row(), filter_col)
            self._tgt_table.setCurrentIndex(editable_idx)
            self._tgt_table.edit(editable_idx)

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

    def _on_target_header_clicked(self, col: int) -> None:
        """Toggle ASC/DESC sort on the clicked column and physically reorder the plan."""
        if self._tgt_sort_col == col:
            # Same column clicked again – flip the direction
            self._tgt_sort_order = (
                Qt.DescendingOrder
                if self._tgt_sort_order == Qt.AscendingOrder
                else Qt.AscendingOrder
            )
        else:
            # New column – start ascending
            self._tgt_sort_col = col
            self._tgt_sort_order = Qt.AscendingOrder
        self._tgt_table.horizontalHeader().setSortIndicator(col, self._tgt_sort_order)
        self._tgt_model.sort(col, self._tgt_sort_order)
        col_name = TargetsModel.HEADERS[col].replace(" ✎", "")
        direction = "A→Z / low→high" if self._tgt_sort_order == Qt.AscendingOrder else "Z→A / high→low"
        self._status.showMessage(f"Sorted by {col_name} ({direction})")

    def _move_target_up(self) -> None:
        rows = [idx.row() for idx in self._tgt_table.selectionModel().selectedRows()]
        if len(rows) == 1:
            row = rows[0]
            self._tgt_model.move_up(row)
            new_row = max(0, row - 1)
            self._tgt_table.selectRow(new_row)
            self._tgt_table.scrollTo(self._tgt_model.index(new_row, 0))
            self._tgt_table.setFocus()

    def _move_target_down(self) -> None:
        rows = [idx.row() for idx in self._tgt_table.selectionModel().selectedRows()]
        if len(rows) == 1:
            row = rows[0]
            n = self._tgt_model.rowCount()
            self._tgt_model.move_down(row)
            new_row = min(row + 1, n - 1)
            self._tgt_table.selectRow(new_row)
            self._tgt_table.scrollTo(self._tgt_model.index(new_row, 0))
            self._tgt_table.setFocus()

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

    def _suggest_exposures(self) -> None:
        """Fill each target's Interval column from the stored exposure calibration.

        Sized for the target's faint end (min_mag) at the default S/N, hard-capped
        so comparison stars never saturate; warns when the bright end (max_mag)
        could saturate.
        """
        from exposure import ExposureCalibration, normalize_telescope, suggest_exposure

        targets = self._tgt_model.get_targets()
        if not targets:
            QMessageBox.information(
                self, "Suggest Exposures",
                "Add targets to the observation plan first."
            )
            return

        telescope = normalize_telescope(self.settings.telescope_name)
        calibs: dict[str, ExposureCalibration] = {}
        for d in self.settings.exposure_calibrations():
            if d.get("telescope") == telescope:
                calibs[d.get("filter_band", "")] = ExposureCalibration.from_dict(d)
        if not calibs:
            QMessageBox.information(
                self, "Suggest Exposures",
                f"No exposure calibration stored for {telescope}.\n\n"
                "Open Analysis → Load Images…, check a plate-solved image from "
                "this telescope and click '📏 Calibrate Exposure' first.",
            )
            return

        updated = 0
        skipped: list[str] = []
        warnings: list[str] = []
        for ot in targets:
            name = ot.aavso.star_name
            faint = ot.aavso.min_mag if ot.aavso.min_mag is not None else ot.aavso.max_mag
            if faint is None:
                skipped.append(f"{name}: no magnitude data")
                continue
            bands = [b.strip() for b in ot.script_filters.split(",") if b.strip()]
            old_ivs = [iv.strip() for iv in ot.script_intervals.split(",")]
            new_ivs: list[str] = []
            missing: set[str] = set()
            changed = False
            for i, band in enumerate(bands):
                calib = calibs.get(band)
                if calib is None:
                    # Keep the existing interval for uncalibrated filters
                    new_ivs.append(old_ivs[i] if i < len(old_ivs) else "30")
                    missing.add(band)
                    continue
                sugg = suggest_exposure(
                    calib, faint_mag=faint, bright_mag=ot.aavso.max_mag
                )
                new_ivs.append(str(sugg.seconds))
                changed = True
                warnings.extend(f"{name} ({band}): {w}" for w in sugg.warnings)
            if missing:
                skipped.append(
                    f"{name}: no {telescope} calibration for {', '.join(sorted(missing))}"
                )
            if changed:
                ot.script_intervals = ",".join(new_ivs)
                updated += 1

        self._tgt_model.set_targets(targets)
        self._status.showMessage(f"Suggested exposures for {updated} target(s)")

        summary = [f"Updated intervals for {updated} of {len(targets)} target(s)."]
        if skipped:
            summary.append("\nSkipped / partial:")
            summary.extend(f"• {s}" for s in skipped[:10])
            if len(skipped) > 10:
                summary.append(f"…and {len(skipped) - 10} more")
        if warnings:
            summary.append("\nWarnings:")
            summary.extend(f"⚠ {w}" for w in warnings[:10])
            if len(warnings) > 10:
                summary.append(f"…and {len(warnings) - 10} more")
        QMessageBox.information(self, "Suggest Exposures", "\n".join(summary))

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

    # ── Database auto-save ───────────────────────────────────────────────────

    def _autosave_plan(self, *_args) -> None:
        """Persist the current targets list after every change."""
        if self._loading_plan:
            return
        try:
            self._db.save_plan(self._tgt_model.get_targets())
        except Exception as exc:  # noqa: BLE001
            self._status.showMessage(f"⚠ DB save failed: {exc}")

    # ── Target summary counter ───────────────────────────────────────────────

    @staticmethod
    def _exposure_for_target(ot: ObservingTarget) -> float:
        """Total shutter-open time (seconds) for one target: sum(count_i * interval_i)."""
        counts: list[int] = []
        for c in ot.script_counts.split(","):
            try:
                counts.append(int(c.strip()))
            except ValueError:
                counts.append(0)
        intervals: list[float] = []
        for iv in ot.script_intervals.split(","):
            try:
                intervals.append(float(iv.strip()))
            except ValueError:
                intervals.append(0.0)
        # Filter and interval lists may differ in length; pair up what we can
        return sum(cnt * iv for cnt, iv in zip(counts, intervals, strict=False))

    def _on_targets_about_to_remove(self, _parent, first: int, last: int) -> None:
        """Subtract exposure of rows about to be deleted (rows still exist here)."""
        for row in range(first, last + 1):
            ot = self._tgt_model.target_at(row)
            if ot:
                self._summary_sec -= self._exposure_for_target(ot)
                self._summary_count -= 1
        self._refresh_summary_label()

    def _on_targets_inserted(self, _parent, first: int, last: int) -> None:
        """Add exposure of newly inserted rows."""
        for row in range(first, last + 1):
            ot = self._tgt_model.target_at(row)
            if ot:
                self._summary_sec += self._exposure_for_target(ot)
                self._summary_count += 1
        self._refresh_summary_label()

    def _recalc_target_summary(self, *_args) -> None:
        """Full recalculate – used after sort, clear, load, or cell edits."""
        targets = self._tgt_model.get_targets()
        self._summary_count = len(targets)
        self._summary_sec = sum(self._exposure_for_target(ot) for ot in targets)
        self._refresh_summary_label()

    def _refresh_summary_label(self) -> None:
        n = self._summary_count
        total_sec = max(0.0, self._summary_sec)  # guard against floating-point drift
        star_str = f"{n} target{'s' if n != 1 else ''}"
        if total_sec <= 0:
            exp_str = "—"
        elif total_sec < 60:
            exp_str = f"{total_sec:.0f} s"
        elif total_sec < 3600:
            exp_str = f"{total_sec / 60:.1f} min"
        else:
            h = int(total_sec // 3600)
            m = int((total_sec % 3600) // 60)
            exp_str = f"{h}h {m:02d}m"
        self._tgt_summary_lbl.setText(f"{star_str}  │  Total exposure: {exp_str}")

    # ── Import from file ────────────────────────────────────────────────────

    # ── Download FITS images ───────────────────────────────────────────────

    def _open_download_dialog(self) -> None:
        from download_dialog import DownloadDialog
        dlg = DownloadDialog(self.settings, self)
        dlg.exec()

    def _open_images_panel(self) -> None:
        from images_panel import ImagesPanel
        workdir = self.settings.analysis_working_dir
        if not workdir or not os.path.isdir(workdir):
            reply = QMessageBox.question(
                self, "No Working Directory",
                "No working directory is set.  Select one now?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                self._select_working_directory()
            workdir = self.settings.analysis_working_dir
            if not workdir:
                return
        # Reuse existing panel if still open; otherwise create a new one
        if self._images_panel is not None and not self._images_panel.isHidden():
            self._images_panel.raise_()
            self._images_panel.activateWindow()
        else:
            self._images_panel = ImagesPanel(workdir, parent=self)
            self._images_panel.show()

    # ── Analysis: working directory ──────────────────────────────────────────

    def _select_working_directory(self) -> None:
        current = self.settings.analysis_working_dir
        start = current if os.path.isdir(current) else os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(
            self, "Select Working Directory for Analysis", start
        )
        if not chosen:
            return
        self.settings.analysis_working_dir = chosen
        self.settings.sync()
        self._refresh_workdir_label()
        # Count FITS files for a useful status message
        fits_count = sum(
            1 for f in os.listdir(chosen)
            if f.lower().endswith((".fit", ".fits", ".fts"))
        )
        self._status.showMessage(
            f"Working directory set: {chosen}  │  {fits_count} FITS file(s)"
        )

    def _clear_working_directory(self) -> None:
        self.settings.analysis_working_dir = ""
        self.settings.sync()
        self._refresh_workdir_label()
        self._status.showMessage("Working directory cleared.")

    def _refresh_workdir_label(self) -> None:
        path = self.settings.analysis_working_dir
        if path and os.path.isdir(path):
            name = os.path.basename(path.rstrip("/\\")) or path
            self._workdir_lbl.setText(f"📂 {name}")
            self._workdir_lbl.setToolTip(f"Working directory: {path}")
            self._workdir_lbl.setStyleSheet(
                "color: #0078d4; font-size: 11px; padding: 0 6px;"
            )
        else:
            self._workdir_lbl.setText("No working dir")
            self._workdir_lbl.setToolTip(
                "No working directory set.  Use Analysis → Select Working Directory…"
            )
            self._workdir_lbl.setStyleSheet(
                "color: gray; font-size: 11px; padding: 0 6px;"
            )

    # ── Import from file ────────────────────────────────────────────────────

    def _import_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Variable Stars from File", "",
            "Text Files (*.txt *.tsv);;All Files (*)",
        )
        if not path:
            return

        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            QMessageBox.critical(self, "File Error", f"Could not read file:\n{exc}")
            return

        defaults = (
            self._def_filter.text().strip() or self.settings.default_filters,
            self._def_count.text().strip()  or self.settings.default_counts,
            self._def_interval.text().strip() or self.settings.default_intervals,
            self._def_binning.text().strip() or self.settings.default_binning,
        )
        targets, errors = _parse_target_text(text, defaults)

        added = skipped = 0
        for ot in targets:
            if self._tgt_model.add_target(ot):
                added += 1
            else:
                skipped += 1

        parts = [f"Imported {added} target(s) from ‘{os.path.basename(path)}’"]
        if skipped:
            parts.append(f"{skipped} already in plan (skipped)")
        if errors:
            parts.append(f"{len(errors)} row(s) skipped")
        parts.append(f"{self._tgt_model.rowCount()} total in plan")
        self._status.showMessage("  |  ".join(parts))

        if errors:
            detail = "\n".join(f"• {e}" for e in errors[:30])
            if len(errors) > 30:
                detail += f"\n… and {len(errors) - 30} more"
            QMessageBox.warning(
                self, "Import Warnings",
                f"Imported {added} target(s).\n"
                f"The following rows could not be parsed:\n\n{detail}",
            )

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
