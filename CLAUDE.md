# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

VSTarget is a PySide6 (Qt) desktop app for variable star observers. It has two workflows:

1. **Planning** – download variable star targets from the AAVSO Target Tool API, build an observation plan, and export it as an iTelescope ACP observing script.
2. **Analysis** – download calibrated FITS images from iTelescope via FTP/SFTP, plate-solve them, optionally stack them, run aperture photometry, and produce an AAVSO WebObs Extended-format report.

## Commands

```powershell
.venv\Scripts\python main.py          # run the app (venv already exists at .venv)
.venv\Scripts\pip install -r requirements.txt   # install/update dependencies
.venv\Scripts\ruff check .            # lint (config in pyproject.toml; E501 ignored)
.venv\Scripts\python -m pytest        # run tests (tests/, config in pyproject.toml)
.venv\Scripts\python -m pytest tests\test_database.py -k round_trip   # single test
```

Tests cover the non-GUI logic (models, exporter, database, import parsing, report formatting, FTP LIST parsing); UI changes still need a manual launch to verify. Run ruff and pytest before committing; code uses modern typing syntax (`list[str]`, `X | None`) — don't reintroduce `typing.List`/`Optional`.

Plate solving requires the external **ASTAP** binary (searched in standard install paths in [platesolve.py](platesolve.py), overridable via the `astap_path` setting).

## Architecture

Flat module layout, no package. All long-running work (API fetches, downloads, plate solving, stacking, photometry) runs in `QThread` subclasses that emit `log` / `progress` / `finished` / `error` signals; dialogs own the thread and wire signals to the UI. Follow this pattern for any new blocking operation.

### Planning workflow
- [main_window.py](main_window.py) – `MainWindow` plus the Qt table models (`VariableListModel`, `TargetsModel`) and script preview dialog. Largest file; most UI logic lives here.
- [models.py](models.py) – `AAVSOTarget` (downloaded star) and `ObservingTarget` (star + per-target script parameters), plus AAVSO section codes and iTelescope location presets.
- [aavso_client.py](aavso_client.py) – REST client (HTTP Basic Auth: API key as username, literal `api_token` as password) + `FetchTargetsThread`.
- [database.py](database.py) – SQLite persistence of the observation plan (auto-saved on every change, restored at launch). DB lives in the OS app-data dir via `QStandardPaths`.
- [script_exporter.py](script_exporter.py) – generates the ACP plan text (targets sorted by RA, tab-delimited name / RA decimal hours / Dec decimal degrees).

### Analysis workflow
- [download_dialog.py](download_dialog.py) + [sftp_downloader.py](sftp_downloader.py) – fetch `calibrated*.fit.zip` files from iTelescope over FTP or SFTP (paramiko), extract, optionally delete from server.
- [images_panel.py](images_panel.py) – modeless dialog listing FITS files in the working directory; launch point for plate solve, stack, and photometry actions.
- [platesolve.py](platesolve.py) – runs the ASTAP CLI, streams its output, verifies WCS keywords were written to the FITS header.
- [stack.py](stack.py) – photometric stacking with astroalign star registration; `create_photometric_stack()` is callable standalone or via `StackThread`.
- [photometry.py](photometry.py) – aperture photometry engine: Simbad target lookup → AAVSO VSP comparison stars → source extraction (SEP with photutils DAOStarFinder fallback) → ensemble linear regression → WebObs Extended report. The reference implementation it mirrors is [notebooks/RWAUR.ipynb](notebooks/RWAUR.ipynb). `match_and_measure()` (WCS star matching + circular-aperture photometry) is factored out for reuse by `transform_generator.py`.
- [report_dialog.py](report_dialog.py) – photometry configuration dialog and report display/save.
- [exposure.py](exposure.py) – exposure calculator: `calibrate_from_image()` measures a telescope/filter throughput model (zeropoint, sky rate, peak fraction) from VSP comp stars in a plate-solved image; `suggest_exposure()` sizes exposures for a target's faint end, hard-capped so comparison stars never saturate. Calibrations persist per telescope+filter as JSON via `SettingsManager`; consumed by the Targets panel's Suggest Exposures button.
- [telescopes_dialog.py](telescopes_dialog.py) – CRUD table editor over the `telescopes` / `telescope_coefficients` DB tables (per-telescope AAVSO UBVRI transformation coefficients, auto-saved on every edit). `COEFFICIENTS` there must stay a superset of `transform_generator.TRANSFORM_DEFS`'s names — `_save()` rewrites the whole table from only its displayed columns, so a transform missing from that list would be silently dropped when the user next edits an unrelated cell. Enforced by `tests/test_telescopes_dialog_coefficients.py`.
- [transform_generator.py](transform_generator.py) – computes photometric transformation coefficients (Henden two-color equations, e.g. Tbv, Tv_bv, ...) from AAVSO standard-field observations (M67, NGC 7790, M11, NGC 1252, NGC 3532, Melotte 111, or a Landolt field). Given one plate-solved FITS image per filter, it downloads the field's catalog magnitudes from AAVSO VSP (`special=std_field`), reuses `photometry.match_and_measure()` per image to get raw instrumental magnitudes for every reference star, then least-squares fits each computable transform. `TransformGeneratorThread` runs it off the UI thread; [transform_generator_dialog.py](transform_generator_dialog.py) is the config/results UI and [transform_review_dialog.py](transform_review_dialog.py) the per-transform interactive outlier-rejection plot (embedded matplotlib `QtAgg` canvas). Results save into the same `telescopes` table `telescopes_dialog.py` edits. Reachable via Analysis → Transformation Coefficient Generator….

### Settings
[settings_manager.py](settings_manager.py) is a property-based `QSettings` wrapper (org "AAVSO", app "VSTarget"); [settings_dialog.py](settings_dialog.py) is its UI. Any new persistent setting gets a property pair in `SettingsManager` and a widget in the settings dialog — don't touch `QSettings` directly elsewhere.

## Conventions

- Optional heavy dependencies (astroalign, SEP, astropy in some modules) are imported in `try/except ImportError` blocks with an `_OK` flag and graceful degradation — keep hard dependencies out of UI modules.
- README.md documents user-facing behavior in detail; update it when changing features it describes.
