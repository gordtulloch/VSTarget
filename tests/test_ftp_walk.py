"""Tests for the FTP directory-walk / LIST-output parser in sftp_downloader.py.

Uses a fake FTP object; no network or Qt event loop involved.
"""
from __future__ import annotations

import ftplib

import pytest

from sftp_downloader import FTPDownloadThread


class FakeFTP:
    """Minimal stand-in serving a static directory tree via LIST (no MLSD)."""

    def __init__(self, tree: dict[str, list[str]]) -> None:
        # tree maps directory path → list of raw LIST lines
        self._tree = tree

    def mlsd(self, path, facts=None):
        raise ftplib.error_perm("500 MLSD not supported")

    def retrlines(self, cmd, callback):
        path = cmd.removeprefix("LIST ").strip()
        if path not in self._tree:
            raise ftplib.error_perm(f"550 {path}: no such directory")
        for line in self._tree[path]:
            callback(line)


def _unix_dir(name: str) -> str:
    return f"drwxr-xr-x  2 user grp  4096 Jan  1 12:00 {name}"


def _unix_file(name: str, size: int = 1234567) -> str:
    return f"-rw-r--r--  1 user grp {size} Jan  1 12:00 {name}"


@pytest.fixture
def thread() -> FTPDownloadThread:
    return FTPDownloadThread("host", 21, "user", "pw", "C:/downloads", False)


def test_walk_finds_calibrated_zips_recursively(thread):
    ftp = FakeFTP(
        {
            "/T24": [_unix_dir("RW Aur"), _unix_file("readme.txt")],
            "/T24/RW Aur": [_unix_dir("20260701")],
            "/T24/RW Aur/20260701": [
                _unix_file("calibrated-T24-RWAur-V.fit.zip"),
                _unix_file("raw-T24-RWAur-V.fit.zip"),  # not calibrated*
                _unix_file("calibrated-T24-RWAur-B.fit.zip"),
            ],
        }
    )
    found = list(thread._walk(ftp, "/T24", depth=0))
    assert found == [
        ("/T24/RW Aur/20260701", "calibrated-T24-RWAur-V.fit.zip"),
        ("/T24/RW Aur/20260701", "calibrated-T24-RWAur-B.fit.zip"),
    ]


def test_walk_parses_windows_iis_listing(thread):
    ftp = FakeFTP(
        {
            "/T05": ["01-01-26  12:00AM       <DIR>          Target"],
            "/T05/Target": [
                "01-01-26  12:05AM              1234567 calibrated-T05-X.fit.zip"
            ],
        }
    )
    found = list(thread._walk(ftp, "/T05", depth=0))
    assert found == [("/T05/Target", "calibrated-T05-X.fit.zip")]


def test_walk_skips_dot_entries(thread):
    ftp = FakeFTP({"/T24": [_unix_dir("."), _unix_dir(".."), _unix_file("calibrated-a.fit.zip")]})
    found = list(thread._walk(ftp, "/T24", depth=0))
    assert found == [("/T24", "calibrated-a.fit.zip")]


def test_walk_respects_max_depth(thread):
    # Build a chain deeper than _MAX_DEPTH (6); the file at the bottom
    # must not be reached.
    tree: dict[str, list[str]] = {}
    path = "/T24"
    for i in range(10):
        tree[path] = [_unix_dir(f"d{i}")]
        path = f"{path}/d{i}"
    tree[path] = [_unix_file("calibrated-deep.fit.zip")]
    found = list(thread._walk(FakeFTP(tree), "/T24", depth=0))
    assert found == []


def test_walk_unlistable_directory_yields_nothing(thread):
    assert list(thread._walk(FakeFTP({}), "/T99", depth=0)) == []


def test_walk_prefers_mlsd_when_available(thread):
    class MlsdFTP:
        def mlsd(self, path, facts=None):
            assert path == "/T24"
            return [
                (".", {"type": "cdir"}),
                ("sub", {"type": "dir"}),
                ("calibrated-x.fit.zip", {"type": "file"}),
            ]

        def retrlines(self, cmd, callback):  # pragma: no cover
            raise AssertionError("LIST fallback should not be used")

    class MlsdLeafFTP(MlsdFTP):
        def mlsd(self, path, facts=None):
            if path == "/T24":
                return super().mlsd(path, facts)
            return []  # empty subdirectory

    found = list(thread._walk(MlsdLeafFTP(), "/T24", depth=0))
    assert found == [("/T24", "calibrated-x.fit.zip")]
