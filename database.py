"""SQLite persistence for the VSTarget observation plan."""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3

from PySide6.QtCore import QStandardPaths

from models import AAVSOTarget, ObservingTarget

# ── Schema version ────────────────────────────────────────────────────────────
_SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_targets (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    sort_order       INTEGER NOT NULL DEFAULT 0,
    -- AAVSO star data
    star_name        TEXT    NOT NULL,
    ra               REAL    NOT NULL,
    dec              REAL    NOT NULL,
    var_type         TEXT    NOT NULL DEFAULT '',
    min_mag          REAL,
    min_mag_band     TEXT    NOT NULL DEFAULT '',
    max_mag          REAL,
    max_mag_band     TEXT    NOT NULL DEFAULT '',
    period           REAL,
    obs_cadence      REAL,
    obs_mode         TEXT    NOT NULL DEFAULT '',
    obs_section      TEXT    NOT NULL DEFAULT '[]',   -- JSON array
    aavso_filters    TEXT    NOT NULL DEFAULT '',
    other_info       TEXT    NOT NULL DEFAULT '',
    last_data_point  INTEGER,
    priority         INTEGER NOT NULL DEFAULT 0,      -- boolean
    constellation    TEXT    NOT NULL DEFAULT '',
    solar_conjunction INTEGER NOT NULL DEFAULT 0,     -- boolean
    -- Per-target script parameters
    script_filters   TEXT    NOT NULL DEFAULT 'V,B,I',
    script_counts    TEXT    NOT NULL DEFAULT '4,4,4',
    script_intervals TEXT    NOT NULL DEFAULT '30,30,30',
    script_binning   TEXT    NOT NULL DEFAULT '1,1,1'
);
"""


def _default_db_path() -> str:
    """Return a platform-appropriate path for the database file."""
    data_dir = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    if not data_dir:
        # Fallback: store next to the script
        data_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "vstTarget.db")


class Database:
    """Manages a SQLite database that persists the observation plan.

    A single connection is kept open for the lifetime of the object so that
    ``check_same_thread=False`` in-memory databases work correctly in tests and
    WAL mode is used efficiently in production.
    """

    def __init__(self, path: str | None = None) -> None:
        self._path = path or _default_db_path()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self._conn.close()

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def path(self) -> str:
        return self._path

    def save_plan(self, targets: list[ObservingTarget]) -> None:
        """Replace the entire saved plan with *targets* (preserves order)."""
        with self._conn:
            self._conn.execute("DELETE FROM plan_targets")
            self._conn.executemany(
                """
                INSERT INTO plan_targets (
                    sort_order, star_name, ra, dec, var_type,
                    min_mag, min_mag_band, max_mag, max_mag_band,
                    period, obs_cadence, obs_mode, obs_section,
                    aavso_filters, other_info, last_data_point,
                    priority, constellation, solar_conjunction,
                    script_filters, script_counts, script_intervals, script_binning
                ) VALUES (
                    :sort_order, :star_name, :ra, :dec, :var_type,
                    :min_mag, :min_mag_band, :max_mag, :max_mag_band,
                    :period, :obs_cadence, :obs_mode, :obs_section,
                    :aavso_filters, :other_info, :last_data_point,
                    :priority, :constellation, :solar_conjunction,
                    :script_filters, :script_counts, :script_intervals, :script_binning
                )
                """,
                [_target_to_row(i, ot) for i, ot in enumerate(targets)],
            )

    def load_plan(self) -> list[ObservingTarget]:
        """Return all saved targets ordered by ``sort_order``."""
        rows = self._conn.execute(
            "SELECT * FROM plan_targets ORDER BY sort_order"
        ).fetchall()
        return [_row_to_target(r) for r in rows]

    def target_count(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM plan_targets"
        ).fetchone()[0]

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(_DDL)
            row = self._conn.execute(
                "SELECT version FROM schema_version"
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (_SCHEMA_VERSION,),
                )
            # Future migrations can be added here based on row["version"]


# ── Row ↔ model conversion ────────────────────────────────────────────────────

def _target_to_row(sort_order: int, ot: ObservingTarget) -> dict:
    t = ot.aavso
    return {
        "sort_order":       sort_order,
        "star_name":        t.star_name,
        "ra":               t.ra,
        "dec":              t.dec,
        "var_type":         t.var_type or "",
        "min_mag":          t.min_mag,
        "min_mag_band":     t.min_mag_band or "",
        "max_mag":          t.max_mag,
        "max_mag_band":     t.max_mag_band or "",
        "period":           t.period,
        "obs_cadence":      t.obs_cadence,
        "obs_mode":         t.obs_mode or "",
        "obs_section":      json.dumps(t.obs_section or []),
        "aavso_filters":    t.filters or "",
        "other_info":       t.other_info or "",
        "last_data_point":  t.last_data_point,
        "priority":         int(bool(t.priority)),
        "constellation":    t.constellation or "",
        "solar_conjunction": int(bool(t.solar_conjunction)),
        "script_filters":   ot.script_filters,
        "script_counts":    ot.script_counts,
        "script_intervals": ot.script_intervals,
        "script_binning":   ot.script_binning,
    }


def _row_to_target(row: sqlite3.Row) -> ObservingTarget:
    t = AAVSOTarget(
        star_name=row["star_name"],
        ra=row["ra"],
        dec=row["dec"],
        var_type=row["var_type"] or "",
        min_mag=row["min_mag"],
        min_mag_band=row["min_mag_band"] or "",
        max_mag=row["max_mag"],
        max_mag_band=row["max_mag_band"] or "",
        period=row["period"],
        obs_cadence=row["obs_cadence"],
        obs_mode=row["obs_mode"] or "",
        obs_section=json.loads(row["obs_section"] or "[]"),
        filters=row["aavso_filters"] or "",
        other_info=row["other_info"] or "",
        last_data_point=row["last_data_point"],
        priority=bool(row["priority"]),
        constellation=row["constellation"] or "",
        solar_conjunction=bool(row["solar_conjunction"]),
    )
    return ObservingTarget(
        aavso=t,
        script_filters=row["script_filters"],
        script_counts=row["script_counts"],
        script_intervals=row["script_intervals"],
        script_binning=row["script_binning"],
    )
