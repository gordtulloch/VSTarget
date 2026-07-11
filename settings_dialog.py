"""Settings dialog: API key, telescope location, and script defaults."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QComboBox,
)
from PySide6.QtCore import Qt

from models import TELESCOPE_PRESETS
from settings_manager import SettingsManager


class SettingsDialog(QDialog):
    """Three-tab dialog for configuring the application."""

    def __init__(self, settings: SettingsManager, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self._build_ui()
        self._load()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        tabs = QTabWidget()
        tabs.addTab(self._build_api_tab(), "API")
        tabs.addTab(self._build_telescope_tab(), "Telescope")
        tabs.addTab(self._build_defaults_tab(), "Script Defaults")
        root.addWidget(tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _build_api_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.Password)
        self._api_key_edit.setPlaceholderText("Paste your AAVSO API key here")

        show_btn = QPushButton("Show")
        show_btn.setCheckable(True)
        show_btn.setFixedWidth(60)
        show_btn.toggled.connect(
            lambda on: self._api_key_edit.setEchoMode(
                QLineEdit.Normal if on else QLineEdit.Password
            )
        )

        key_row = QHBoxLayout()
        key_row.addWidget(self._api_key_edit)
        key_row.addWidget(show_btn)
        form.addRow("API Key:", key_row)

        info = QLabel(
            'Don\'t have a key? '
            '<a href="https://targettool.aavso.org/TargetTool/default/user/register">'
            'Register at AAVSO Target Tool</a>'
        )
        info.setOpenExternalLinks(True)
        info.setTextFormat(Qt.RichText)
        form.addRow("", info)
        return tab

    def _build_telescope_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset:"))
        self._preset_combo = QComboBox()
        self._preset_combo.addItems(list(TELESCOPE_PRESETS.keys()))
        self._preset_combo.currentTextChanged.connect(self._on_preset_changed)
        preset_row.addWidget(self._preset_combo, 1)
        layout.addLayout(preset_row)

        box = QGroupBox("Location")
        form = QFormLayout(box)

        self._lat_spin = QDoubleSpinBox()
        self._lat_spin.setRange(-90.0, 90.0)
        self._lat_spin.setDecimals(4)
        self._lat_spin.setSuffix("°")
        self._lat_spin.setToolTip("North positive, South negative")

        self._lon_spin = QDoubleSpinBox()
        self._lon_spin.setRange(-180.0, 180.0)
        self._lon_spin.setDecimals(4)
        self._lon_spin.setSuffix("°")
        self._lon_spin.setToolTip("East positive, West negative")

        self._alt_spin = QDoubleSpinBox()
        self._alt_spin.setRange(0.0, 90.0)
        self._alt_spin.setDecimals(1)
        self._alt_spin.setSuffix("°")
        self._alt_spin.setToolTip("Minimum altitude above horizon for a target to be considered observable")

        self._sun_spin = QDoubleSpinBox()
        self._sun_spin.setRange(-18.0, 0.0)
        self._sun_spin.setDecimals(1)
        self._sun_spin.setSuffix("°")
        self._sun_spin.setToolTip("Sun altitude defining dusk/dawn (typically −5° to −12°)")

        form.addRow("Latitude:", self._lat_spin)
        form.addRow("Longitude:", self._lon_spin)
        form.addRow("Min target altitude:", self._alt_spin)
        form.addRow("Sun altitude (dusk/dawn):", self._sun_spin)
        layout.addWidget(box)
        layout.addStretch()
        return tab

    def _build_defaults_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self._def_filter = QLineEdit()
        self._def_count = QLineEdit()
        self._def_interval = QLineEdit()
        self._def_binning = QLineEdit()

        form.addRow("Filter(s):", self._def_filter)
        form.addRow("Count(s):", self._def_count)
        form.addRow("Interval(s) [s]:", self._def_interval)
        form.addRow("Binning:", self._def_binning)

        hint = QLabel(
            "Use comma-separated values for multi-filter setups.\n"
            "All four fields must have the same number of values.\n"
            "Example:  V,B,I  /  4,4,4  /  30,30,30  /  1,1,1"
        )
        hint.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow("", hint)
        return tab

    # ── Data load / save ──────────────────────────────────────────────────────

    def _load(self) -> None:
        self._api_key_edit.setText(self.settings.api_key)

        name = self.settings.telescope_name
        idx = self._preset_combo.findText(name)
        self._preset_combo.setCurrentIndex(idx if idx >= 0 else 0)

        self._lat_spin.setValue(self.settings.latitude)
        self._lon_spin.setValue(self.settings.longitude)
        self._alt_spin.setValue(self.settings.target_altitude)
        self._sun_spin.setValue(self.settings.sun_altitude)

        self._def_filter.setText(self.settings.default_filters)
        self._def_count.setText(self.settings.default_counts)
        self._def_interval.setText(self.settings.default_intervals)
        self._def_binning.setText(self.settings.default_binning)

    def _on_preset_changed(self, name: str) -> None:
        preset = TELESCOPE_PRESETS.get(name)
        if preset and name != "Custom":
            self._lat_spin.setValue(preset["latitude"])
            self._lon_spin.setValue(preset["longitude"])
            self._alt_spin.setValue(preset["target_altitude"])
            self._sun_spin.setValue(preset["sun_altitude"])

    def _save_and_accept(self) -> None:
        self.settings.api_key = self._api_key_edit.text().strip()
        self.settings.telescope_name = self._preset_combo.currentText()
        self.settings.latitude = self._lat_spin.value()
        self.settings.longitude = self._lon_spin.value()
        self.settings.target_altitude = self._alt_spin.value()
        self.settings.sun_altitude = self._sun_spin.value()
        self.settings.default_filters = self._def_filter.text().strip()
        self.settings.default_counts = self._def_count.text().strip()
        self.settings.default_intervals = self._def_interval.text().strip()
        self.settings.default_binning = self._def_binning.text().strip()
        self.settings.sync()
        self.accept()
