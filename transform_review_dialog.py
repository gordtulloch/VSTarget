"""Interactive review of one photometric transformation coefficient.

Click a point in the standard-vs-instrumental colour-difference scatter
plot to include/exclude it from the least-squares fit; the fit line and
reported slope/error/R^2 update live.  Mirrors the outlier-rejection
workflow from the legacy TG tool's plot window.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("QtAgg")
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from transform_generator import TransformResult


class TransformReviewDialog(QDialog):
    """Modal dialog for one transform: interactive outlier rejection."""

    def __init__(self, result: TransformResult, parent=None) -> None:
        super().__init__(parent)
        self._result = result
        self._artist_index: dict[int, int] = {}
        self.setWindowTitle(f"Review Transform – {result.name}")
        self.resize(640, 660)
        self._build_ui()
        self._redraw()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        self._summary_lbl = QLabel()
        self._summary_lbl.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 12px; padding: 4px;"
        )
        root.addWidget(self._summary_lbl)

        self._figure = Figure(figsize=(6, 6), dpi=100)
        self._canvas = FigureCanvasQTAgg(self._figure)
        self._canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._ax = self._figure.add_subplot(111)
        self._canvas.mpl_connect("pick_event", self._on_pick)
        root.addWidget(self._canvas, 1)

        hint = QLabel(
            "Click a point to include/exclude it from the fit "
            "(green = used, red = excluded)."
        )
        hint.setStyleSheet("color: gray; font-size: 11px;")
        root.addWidget(hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── Interaction ────────────────────────────────────────────────────────────

    def _on_pick(self, event) -> None:
        idx = self._artist_index.get(id(event.artist))
        if idx is None:
            return
        point = self._result.points[idx]
        point.used = not point.used
        self._result.recompute()
        self._redraw()

    # ── Plotting ───────────────────────────────────────────────────────────────

    def _redraw(self) -> None:
        r = self._result
        self._ax.clear()
        self._artist_index = {}

        for i, p in enumerate(r.points):
            color = "green" if p.used else "red"
            (artist,) = self._ax.plot(p.x, p.y, "o", color=color, picker=5)
            self._artist_index[id(artist)] = i

        used = [p for p in r.points if p.used]
        if len(used) >= 2:
            x_arr = np.array([p.x for p in used])
            y_arr = np.array([p.y for p in used])
            slope, intercept = np.polyfit(x_arr, y_arr, 1)
            x_min, x_max = float(x_arr.min()), float(x_arr.max())
            self._ax.plot(
                [x_min, x_max],
                [slope * x_min + intercept, slope * x_max + intercept],
                "r-", linewidth=1.5,
            )

        self._ax.set_xlabel(r.x_label)
        self._ax.set_ylabel(r.y_label)
        self._ax.set_title(r.name)
        self._canvas.draw_idle()

        self._summary_lbl.setText(
            f"{r.name}  =  {r.value:.4f}   err = {r.error:.4f}   "
            f"R² = {r.r_squared:.3f}   n = {len(used)}/{len(r.points)}"
        )
