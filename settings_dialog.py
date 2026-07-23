"""Settings dialog: API key, telescope location, and script defaults."""
from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
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
        tabs.addTab(self._build_download_tab(), "Download")
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

        self._observer_code_edit = QLineEdit()
        self._observer_code_edit.setPlaceholderText("e.g. TGOR")
        form.addRow("AAVSO Observer Code:", self._observer_code_edit)

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
    def _build_download_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self._sftp_host = QLineEdit()
        self._sftp_host.setPlaceholderText("data.itelescope.net")
        form.addRow("SFTP Host:", self._sftp_host)

        self._sftp_port = QSpinBox()
        self._sftp_port.setRange(1, 65535)
        self._sftp_port.setValue(22)
        form.addRow("Port:", self._sftp_port)

        self._sftp_user = QLineEdit()
        self._sftp_user.setPlaceholderText("iTelescope username")
        form.addRow("Username:", self._sftp_user)

        pw_row = QHBoxLayout()
        self._sftp_pw = QLineEdit()
        self._sftp_pw.setEchoMode(QLineEdit.Password)
        self._sftp_pw.setPlaceholderText("Password")
        show_btn = QPushButton("Show")
        show_btn.setCheckable(True)
        show_btn.setFixedWidth(55)
        show_btn.toggled.connect(
            lambda on: self._sftp_pw.setEchoMode(
                QLineEdit.Normal if on else QLineEdit.Password
            )
        )
        pw_row.addWidget(self._sftp_pw)
        pw_row.addWidget(show_btn)
        form.addRow("Password:", pw_row)

        path_row = QHBoxLayout()
        self._sftp_path = QLineEdit()
        self._sftp_path.setPlaceholderText("Local folder to save images")
        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedWidth(75)
        browse_btn.clicked.connect(self._browse_download_path)
        path_row.addWidget(self._sftp_path)
        path_row.addWidget(browse_btn)
        form.addRow("Download to:", path_row)

        self._sftp_delete = QCheckBox(
            "Delete files from server after successful download"
        )
        form.addRow("", self._sftp_delete)

        note = QLabel(
            "⚠  Password is stored in plaintext by QSettings.  "
            "Avoid using your main account password on shared machines."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow("", note)

        # ASTAP plate solver
        from PySide6.QtWidgets import QGroupBox as _GB
        astap_box = _GB("Plate Solver (ASTAP)")
        astap_form = QFormLayout(astap_box)
        astap_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        astap_row = QHBoxLayout()
        self._astap_path = QLineEdit()
        self._astap_path.setPlaceholderText(
            r"C:\Program Files\astap\astap.exe" if os.name == "nt" else "/usr/bin/astap"
        )
        astap_browse = QPushButton("Browse…")
        astap_browse.setFixedWidth(75)
        astap_browse.clicked.connect(self._browse_astap)
        astap_detect = QPushButton("Auto-detect")
        astap_detect.clicked.connect(self._detect_astap)
        astap_row.addWidget(self._astap_path)
        astap_row.addWidget(astap_browse)
        astap_row.addWidget(astap_detect)
        astap_form.addRow("ASTAP path:", astap_row)
        form.addRow(astap_box)

        # Photometry report
        report_box = _GB("Photometry Report")
        report_form = QFormLayout(report_box)
        report_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self._lightcurve_days = QSpinBox()
        self._lightcurve_days.setRange(1, 3650)
        self._lightcurve_days.setValue(10)
        self._lightcurve_days.setSuffix(" days")
        self._lightcurve_days.setToolTip(
            "How far back to fetch AAVSO archive observations for the "
            "light-curve chart shown after each analysis."
        )
        report_form.addRow("Light curve lookback:", self._lightcurve_days)
        form.addRow(report_box)
        return tab

    def _browse_download_path(self) -> None:
        current = self._sftp_path.text().strip()
        start = current if os.path.isdir(current) else os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, "Select Download Folder", start)
        if chosen:
            self._sftp_path.setText(chosen)

    def _browse_astap(self) -> None:
        current = self._astap_path.text().strip()
        start = os.path.dirname(current) if current else os.path.expanduser("~")
        ext = "Executables (*.exe)" if os.name == "nt" else "All Files (*)"
        path, _ = QFileDialog.getOpenFileName(self, "Locate ASTAP Executable", start, ext)
        if path:
            self._astap_path.setText(path)

    def _detect_astap(self) -> None:
        from platesolve import find_astap
        found = find_astap()
        if found:
            self._astap_path.setText(found)
        else:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "ASTAP Not Found",
                "ASTAP was not found on PATH or in the default installation folders.\n"
                "Download it from https://www.hnsky.org/astap.htm"
            )
    def _load(self) -> None:
        self._api_key_edit.setText(self.settings.api_key)
        self._observer_code_edit.setText(self.settings.observer_code)

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

        self._sftp_host.setText(self.settings.sftp_host)
        self._sftp_port.setValue(self.settings.sftp_port)
        self._sftp_user.setText(self.settings.sftp_username)
        self._sftp_pw.setText(self.settings.sftp_password)
        self._sftp_path.setText(self.settings.sftp_download_path)
        self._sftp_delete.setChecked(self.settings.sftp_delete_after)
        self._astap_path.setText(self.settings.astap_path)
        self._lightcurve_days.setValue(self.settings.lightcurve_days)

    def _on_preset_changed(self, name: str) -> None:
        preset = TELESCOPE_PRESETS.get(name)
        if preset and name != "Custom":
            self._lat_spin.setValue(preset["latitude"])
            self._lon_spin.setValue(preset["longitude"])
            self._alt_spin.setValue(preset["target_altitude"])
            self._sun_spin.setValue(preset["sun_altitude"])

    def _save_and_accept(self) -> None:
        self.settings.api_key = self._api_key_edit.text().strip()
        self.settings.observer_code = self._observer_code_edit.text().strip()
        self.settings.telescope_name = self._preset_combo.currentText()
        self.settings.latitude = self._lat_spin.value()
        self.settings.longitude = self._lon_spin.value()
        self.settings.target_altitude = self._alt_spin.value()
        self.settings.sun_altitude = self._sun_spin.value()
        self.settings.default_filters = self._def_filter.text().strip()
        self.settings.default_counts = self._def_count.text().strip()
        self.settings.default_intervals = self._def_interval.text().strip()
        self.settings.default_binning = self._def_binning.text().strip()
        self.settings.sftp_host = self._sftp_host.text().strip()
        self.settings.sftp_port = self._sftp_port.value()
        self.settings.sftp_username = self._sftp_user.text().strip()
        self.settings.sftp_password = self._sftp_pw.text()
        self.settings.sftp_download_path = self._sftp_path.text().strip()
        self.settings.sftp_delete_after = self._sftp_delete.isChecked()
        self.settings.astap_path = self._astap_path.text().strip()
        self.settings.lightcurve_days = self._lightcurve_days.value()
        self.settings.sync()
        self.accept()
