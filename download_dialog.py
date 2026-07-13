"""Download dialog: FTP/SFTP retrieval of calibrated FITS images from iTelescope."""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)
from PySide6.QtGui import QFont

from settings_manager import SettingsManager
from sftp_downloader import FTPDownloadThread, SFTPDownloadThread

_PROTOCOL_FTP = "FTP"
_PROTOCOL_SFTP = "SFTP"
_DEFAULT_PORTS = {_PROTOCOL_FTP: 21, _PROTOCOL_SFTP: 22}


class DownloadDialog(QDialog):
    """Manages FTP/SFTP download of calibrated FITS files from the iTelescope server.

    Connection settings are read from (and saved back to) *SettingsManager*.
    The dialog can be re-used: clicking *Start Download* multiple times will
    re-scan the server and download any new files.
    """

    def __init__(self, settings: SettingsManager, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._thread: Optional[SFTPDownloadThread] = None
        self.setWindowTitle("Download FITS Images – iTelescope SFTP")
        self.setMinimumWidth(700)
        self.resize(740, 620)
        self._build_ui()
        self._load_settings()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # ── Connection settings ───────────────────────────────────────────────
        conn_box = QGroupBox("Connection && Download Settings")
        conn_form = QFormLayout(conn_box)
        conn_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        # Protocol selector
        self._proto_combo = QComboBox()
        self._proto_combo.addItem("FTP + Explicit TLS (FTPES) — falls back to plain FTP", _PROTOCOL_FTP)
        self._proto_combo.addItem("SFTP (SSH / Secure Shell)", _PROTOCOL_SFTP)
        self._proto_combo.currentIndexChanged.connect(self._on_protocol_changed)
        conn_form.addRow("Protocol:", self._proto_combo)

        self._host_edit = QLineEdit()
        self._host_edit.setPlaceholderText("data.itelescope.net")
        conn_form.addRow("Host:", self._host_edit)

        port_user_row = QHBoxLayout()
        self._port_edit = QLineEdit()
        self._port_edit.setFixedWidth(70)
        self._port_edit.setPlaceholderText("21")
        port_user_row.addWidget(self._port_edit)
        port_user_row.addWidget(QLabel("  Username:"))
        self._user_edit = QLineEdit()
        self._user_edit.setPlaceholderText("iTelescope username")
        port_user_row.addWidget(self._user_edit, 1)
        conn_form.addRow("Port:", port_user_row)

        pw_row = QHBoxLayout()
        self._pw_edit = QLineEdit()
        self._pw_edit.setEchoMode(QLineEdit.Password)
        self._pw_edit.setPlaceholderText("Password")
        show_btn = QPushButton("Show")
        show_btn.setCheckable(True)
        show_btn.setFixedWidth(55)
        show_btn.toggled.connect(
            lambda on: self._pw_edit.setEchoMode(
                QLineEdit.Normal if on else QLineEdit.Password
            )
        )
        pw_row.addWidget(self._pw_edit)
        pw_row.addWidget(show_btn)
        conn_form.addRow("Password:", pw_row)

        path_row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Local folder to save images")
        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedWidth(75)
        browse_btn.clicked.connect(self._browse_path)
        path_row.addWidget(self._path_edit)
        path_row.addWidget(browse_btn)
        conn_form.addRow("Download to:", path_row)

        self._delete_cb = QCheckBox(
            "Delete files from server after successful download and extraction"
        )
        conn_form.addRow("", self._delete_cb)

        save_btn = QPushButton("Save Settings")
        save_btn.setToolTip("Persist connection details for future sessions")
        save_btn.clicked.connect(self._save_settings)
        conn_form.addRow("", save_btn)

        root.addWidget(conn_box)

        # ── Log ───────────────────────────────────────────────────────────────
        root.addWidget(QLabel("Download log:"))
        mono = QFont("Consolas", 9)
        mono.setStyleHint(QFont.Monospace)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(mono)
        root.addWidget(self._log, 1)

        # ── Progress ──────────────────────────────────────────────────────────
        prog_box = QGroupBox("Progress")
        prog_layout = QVBoxLayout(prog_box)

        self._file_lbl = QLabel("Current file: —")
        prog_layout.addWidget(self._file_lbl)
        self._file_bar = QProgressBar()
        self._file_bar.setTextVisible(True)
        self._file_bar.setVisible(False)
        prog_layout.addWidget(self._file_bar)

        self._overall_lbl = QLabel("Overall: —")
        prog_layout.addWidget(self._overall_lbl)
        self._overall_bar = QProgressBar()
        self._overall_bar.setValue(0)
        prog_layout.addWidget(self._overall_bar)

        root.addWidget(prog_box)

        # ── Action buttons ────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("▶  Start Download")
        self._start_btn.setDefault(True)
        self._start_btn.clicked.connect(self._start)

        self._stop_btn = QPushButton("■  Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)

        btn_row.addWidget(self._start_btn)
        btn_row.addWidget(self._stop_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── Settings load / save ──────────────────────────────────────────────────

    def _load_settings(self) -> None:
        proto = self.settings.download_protocol
        idx = self._proto_combo.findData(proto)
        self._proto_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._host_edit.setText(self.settings.sftp_host)
        self._port_edit.setText(str(self.settings.sftp_port))
        self._user_edit.setText(self.settings.sftp_username)
        self._pw_edit.setText(self.settings.sftp_password)
        self._path_edit.setText(self.settings.sftp_download_path)
        self._delete_cb.setChecked(self.settings.sftp_delete_after)

    def _save_settings(self) -> None:
        self.settings.download_protocol = self._proto_combo.currentData()
        self.settings.sftp_host = self._host_edit.text().strip()
        try:
            self.settings.sftp_port = int(self._port_edit.text().strip() or "22")
        except ValueError:
            self.settings.sftp_port = 22
        self.settings.sftp_username = self._user_edit.text().strip()
        self.settings.sftp_password = self._pw_edit.text()
        self.settings.sftp_download_path = self._path_edit.text().strip()
        self.settings.sftp_delete_after = self._delete_cb.isChecked()
        self.settings.sync()
        self._append_log("Settings saved.")

    def _browse_path(self) -> None:
        current = self._path_edit.text().strip()
        start = current if os.path.isdir(current) else os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(
            self, "Select Download Folder", start
        )
        if chosen:
            self._path_edit.setText(chosen)

    def _on_protocol_changed(self) -> None:
        """When the protocol changes, suggest the canonical default port."""
        proto = self._proto_combo.currentData()
        current_port = self._port_edit.text().strip()
        # Only auto-update if the port is still one of the two default values
        if current_port in ("21", "22", ""):
            self._port_edit.setText(str(_DEFAULT_PORTS.get(proto, 21)))

    # ── Download control ──────────────────────────────────────────────────────

    def _start(self) -> None:
        host = self._host_edit.text().strip()
        try:
            port = int(self._port_edit.text().strip() or "22")
        except ValueError:
            port = 22
        username = self._user_edit.text().strip()
        password = self._pw_edit.text()
        download_path = self._path_edit.text().strip()

        if not host or not username or not password:
            QMessageBox.warning(
                self, "Missing Credentials",
                "Please enter host, username, and password.",
            )
            return
        if not download_path:
            QMessageBox.warning(
                self, "No Download Folder",
                "Please choose a local folder to save images.",
            )
            return

        self._save_settings()
        self._log.clear()
        self._reset_progress()

        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

        proto = self._proto_combo.currentData()
        ThreadClass = FTPDownloadThread if proto == _PROTOCOL_FTP else SFTPDownloadThread

        self._thread = ThreadClass(
            host=host,
            port=port,
            username=username,
            password=password,
            download_path=download_path,
            delete_after=self._delete_cb.isChecked(),
            parent=self,
        )
        self._thread.log.connect(self._append_log)
        self._thread.file_started.connect(self._on_file_started)
        self._thread.file_bytes.connect(self._on_file_bytes)
        self._thread.overall.connect(self._on_overall)
        self._thread.finished.connect(self._on_finished)
        self._thread.error.connect(self._on_error)
        self._thread.start()

    def _stop(self) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.stop()
            self._stop_btn.setEnabled(False)
            self._append_log("Stop requested — waiting for current file to complete…")

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _append_log(self, msg: str) -> None:
        self._log.append(msg)
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _reset_progress(self) -> None:
        self._file_bar.setVisible(False)
        self._file_lbl.setText("Current file: —")
        self._overall_bar.setValue(0)
        self._overall_bar.setMaximum(1)
        self._overall_lbl.setText("Overall: —")

    def _on_file_started(self, remote_path: str, total_bytes: int) -> None:
        name = remote_path.rsplit("/", 1)[-1]
        mb = total_bytes / 1_048_576
        self._file_lbl.setText(f"Current file: {name}  ({mb:.1f} MB)")
        self._file_bar.setMaximum(max(total_bytes, 1))
        self._file_bar.setValue(0)
        self._file_bar.setVisible(True)

    def _on_file_bytes(self, transferred: int, total: int) -> None:
        self._file_bar.setMaximum(max(total, 1))
        self._file_bar.setValue(transferred)

    def _on_overall(self, done: int, total: int) -> None:
        self._overall_bar.setMaximum(max(total, 1))
        self._overall_bar.setValue(done)
        self._overall_lbl.setText(f"Overall: {done} / {total} files")

    def _on_finished(self, downloaded: int, skipped: int) -> None:
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._file_bar.setVisible(False)
        self._file_lbl.setText("Current file: —")
        self._append_log(
            f"\n✓  Done — Downloaded: {downloaded}  |  Skipped: {skipped}"
        )

    def _on_error(self, msg: str) -> None:
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._append_log(f"\n✗  ERROR: {msg}")
        QMessageBox.critical(self, "Download Error", msg)

    # ── Window close ──────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._thread and self._thread.isRunning():
            self._thread.stop()
            self._thread.wait(5000)
        event.accept()
