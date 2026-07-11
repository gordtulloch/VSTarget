"""Entry point for the VSTarget application."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("VSTarget")
    app.setApplicationDisplayName("VSTarget – AAVSO Variable Star Observation Planner")
    app.setOrganizationName("AAVSO")
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
