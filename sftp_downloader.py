"""FTP and SFTP download threads for retrieving calibrated FITS files from iTelescope."""
from __future__ import annotations

import fnmatch
import ftplib
import os
import re
import stat
import zipfile
from typing import Iterator, List, Tuple

import paramiko
from PySide6.QtCore import QThread, Signal

# ── Constants ─────────────────────────────────────────────────────────────────

SFTP_PORT = 22
_TELESCOPE_RE = re.compile(r"^T\d{2}$", re.IGNORECASE)
_FILE_GLOB = "calibrated*.fit.zip"
_MAX_DEPTH = 6   # guard against pathological nesting


# ── Worker thread ─────────────────────────────────────────────────────────────

class SFTPDownloadThread(QThread):
    """Background thread that downloads *calibrated\\*.fit.zip* files from
    the iTelescope SFTP server, extracts them, and (optionally) removes them
    from the server.

    Signals
    -------
    log(str)                – Human-readable status / progress line.
    file_started(str, int)  – Remote path, total file size in bytes.
    file_bytes(int, int)    – Bytes transferred so far, total bytes.
    overall(int, int)       – Files processed so far, total files found.
    finished(int, int)      – Downloaded count, skipped count.
    error(str)              – Fatal error message (connection failure etc.).
    """

    log = Signal(str)
    file_started = Signal(str, int)
    file_bytes = Signal(int, int)
    overall = Signal(int, int)
    finished = Signal(int, int)
    error = Signal(str)

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        download_path: str,
        delete_after: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.download_path = download_path
        self.delete_after = delete_after
        self._stop = False

    def stop(self) -> None:
        """Request a graceful stop after the current file completes."""
        self._stop = True

    # ── QThread.run ───────────────────────────────────────────────────────────

    def run(self) -> None:
        client = paramiko.SSHClient()
        # AutoAddPolicy is appropriate here: users connect to a known
        # commercial service and manual host-key verification is impractical.
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            self.log.emit(f"Connecting to {self.host}:{self.port}…")
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                timeout=30,
                banner_timeout=30,
            )
            sftp = client.open_sftp()
        except paramiko.AuthenticationException:
            self.error.emit("Authentication failed — check username and password.")
            return
        except Exception as exc:
            self.error.emit(f"Connection failed: {exc}")
            return

        try:
            self._run_transfer(sftp)
        finally:
            try:
                sftp.close()
                client.close()
            except Exception:
                pass

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _run_transfer(self, sftp: paramiko.SFTPClient) -> None:
        self.log.emit("Connected.  Scanning telescope folders…")

        # Find top-level T?? directories
        try:
            root_entries = sftp.listdir("/")
        except IOError as exc:
            self.error.emit(f"Cannot list root directory: {exc}")
            return

        tel_dirs = sorted(e for e in root_entries if _TELESCOPE_RE.match(e))
        if not tel_dirs:
            self.log.emit("No telescope folders (T??) found on server.")
            self.finished.emit(0, 0)
            return

        self.log.emit(
            f"Found {len(tel_dirs)} telescope folder(s): {', '.join(tel_dirs)}"
        )

        # Collect the full list before starting to download so we can show
        # accurate overall progress.
        all_files: list[Tuple[str, str]] = []
        for td in tel_dirs:
            if self._stop:
                break
            self.log.emit(f"  Scanning /{td}…")
            for item in self._walk(sftp, f"/{td}", depth=0):
                all_files.append(item)

        total = len(all_files)
        self.log.emit(f"Found {total} matching file(s) total.")
        self.overall.emit(0, total)

        downloaded = 0
        skipped = 0

        for idx, (remote_dir, filename) in enumerate(all_files, start=1):
            if self._stop:
                self.log.emit("Stopped by user.")
                break

            remote_path = f"{remote_dir}/{filename}"
            fit_name = filename.removesuffix(".zip")

            # Mirror the remote directory structure locally
            rel_subdir = remote_dir.lstrip("/")
            local_dir = os.path.join(self.download_path, rel_subdir)
            local_fit = os.path.join(local_dir, fit_name)
            local_zip = os.path.join(local_dir, filename)

            # Skip if the extracted FITS file already exists
            if os.path.exists(local_fit):
                self.log.emit(f"  SKIP   {remote_path}")
                skipped += 1
                self.overall.emit(idx, total)
                continue

            os.makedirs(local_dir, exist_ok=True)

            # Get file size for progress display
            try:
                file_size = sftp.stat(remote_path).st_size or 0
            except Exception:
                file_size = 0

            size_mb = file_size / 1_048_576
            self.log.emit(
                f"  DL     {remote_path}  ({size_mb:.1f} MB)"
            )
            self.file_started.emit(remote_path, file_size)

            # Download
            try:
                sftp.get(
                    remote_path,
                    local_zip,
                    callback=lambda done, total_b, fs=file_size: (
                        self.file_bytes.emit(done, total_b if total_b else fs)
                    ),
                )
            except Exception as exc:
                self.log.emit(f"  ERROR  downloading {remote_path}: {exc}")
                # Remove a partial download if it exists
                if os.path.exists(local_zip):
                    try:
                        os.remove(local_zip)
                    except OSError:
                        pass
                self.overall.emit(idx, total)
                continue

            # Unzip
            try:
                with zipfile.ZipFile(local_zip, "r") as zf:
                    zf.extractall(local_dir)
                os.remove(local_zip)
                self.log.emit(f"  UNZIP  {fit_name}")
            except Exception as exc:
                self.log.emit(f"  ERROR  unzipping {local_zip}: {exc}")
                self.overall.emit(idx, total)
                continue

            # Optionally delete from server
            if self.delete_after:
                try:
                    sftp.remove(remote_path)
                    self.log.emit(f"  DEL    {remote_path} (removed from server)")
                except Exception as exc:
                    self.log.emit(
                        f"  WARN   Could not delete {remote_path} from server: {exc}"
                    )

            downloaded += 1
            self.overall.emit(idx, total)

        self.log.emit(
            f"\nFinished — Downloaded: {downloaded}  |  Skipped: {skipped}"
        )
        self.finished.emit(downloaded, skipped)

    def _walk(
        self, sftp: paramiko.SFTPClient, remote_dir: str, depth: int
    ) -> Iterator[Tuple[str, str]]:
        """Recursively yield (remote_dir, filename) for files matching the
        calibrated FITS glob pattern."""
        if depth > _MAX_DEPTH:
            return
        try:
            entries = sftp.listdir_attr(remote_dir)
        except IOError:
            return
        for entry in entries:
            if entry.st_mode is None:
                continue
            child_path = f"{remote_dir}/{entry.filename}"
            if stat.S_ISDIR(entry.st_mode):
                yield from self._walk(sftp, child_path, depth + 1)
            elif fnmatch.fnmatch(entry.filename, _FILE_GLOB):
                yield remote_dir, entry.filename


# ── FTP worker thread (explicit TLS → plain FTP fallback) ────────────────────

FTP_PORT = 21


class FTPDownloadThread(QThread):
    """Background thread that downloads *calibrated\\*.fit.zip* files using FTP.

    On connect, explicit TLS (``AUTH TLS`` / FTPES) is attempted first.  If the
    server rejects TLS the thread logs a warning and continues with an
    unencrypted plain-FTP session.

    The same signals as :class:`SFTPDownloadThread` are emitted so the dialog
    is protocol-agnostic.
    """

    log = Signal(str)
    file_started = Signal(str, int)
    file_bytes = Signal(int, int)
    overall = Signal(int, int)
    finished = Signal(int, int)
    error = Signal(str)

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        download_path: str,
        delete_after: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.host = host
        self.port = port if port else FTP_PORT
        self.username = username
        self.password = password
        self.download_path = download_path
        self.delete_after = delete_after
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    # ── QThread.run ───────────────────────────────────────────────────────────

    def run(self) -> None:
        ftp = self._connect()
        if ftp is None:
            return
        try:
            self._run_transfer(ftp)
        finally:
            try:
                ftp.quit()
            except Exception:
                pass

    # ── Connection ────────────────────────────────────────────────────────────

    def _connect(self):
        """Try explicit TLS first; fall back to plain FTP."""
        self.log.emit(
            f"Connecting to {self.host}:{self.port} (trying explicit TLS)…"
        )

        # ── Attempt 1: FTPES (explicit TLS via AUTH TLS) ──────────────────────
        ftp_tls = ftplib.FTP_TLS(timeout=30)
        try:
            ftp_tls.connect(self.host, self.port)
            # FTP_TLS.login() calls AUTH TLS automatically before sending
            # credentials when secure=True (the default).
            ftp_tls.login(self.username, self.password)
            ftp_tls.prot_p()        # protect data channel as well
            ftp_tls.set_pasv(True)
            self.log.emit(
                "Connected with Explicit TLS (FTPES) — control and data channels encrypted."
            )
            return ftp_tls
        except ftplib.error_perm as exc:
            # 530 = bad credentials; no point retrying plain FTP.
            if str(exc).startswith("530"):
                self.error.emit(
                    "Authentication failed (FTP 530) — check username and password."
                )
                try:
                    ftp_tls.quit()
                except Exception:
                    pass
                return None
            self.log.emit(f"TLS not accepted by server ({exc}); retrying plain FTP…")
        except Exception as exc:
            self.log.emit(f"TLS unavailable ({exc}); retrying plain FTP…")
        try:
            ftp_tls.quit()
        except Exception:
            pass

        # ── Attempt 2: plain FTP ──────────────────────────────────────────────
        try:
            ftp = ftplib.FTP(timeout=30)
            ftp.connect(self.host, self.port)
            ftp.login(self.username, self.password)
            ftp.set_pasv(True)
            self.log.emit("Connected with plain FTP (unencrypted).")
            return ftp
        except ftplib.error_perm as exc:
            self.error.emit(
                f"Authentication failed — check username and password.  ({exc})"
            )
        except Exception as exc:
            self.error.emit(f"Connection failed: {exc}")
        return None

    # ── Transfer ──────────────────────────────────────────────────────────────

    def _run_transfer(self, ftp) -> None:
        self.log.emit("Scanning telescope folders…")

        # List root to find T?? directories
        try:
            root_entries = ftp.nlst("/")
        except Exception as exc:
            self.error.emit(f"Cannot list root directory: {exc}")
            return

        def _basename(path: str) -> str:
            return path.rstrip("/").split("/")[-1]

        tel_dirs = sorted(
            _basename(e)
            for e in root_entries
            if _TELESCOPE_RE.match(_basename(e))
        )

        if not tel_dirs:
            self.log.emit("No telescope folders (T??) found on server.")
            self.finished.emit(0, 0)
            return

        self.log.emit(
            f"Found {len(tel_dirs)} telescope folder(s): {', '.join(tel_dirs)}"
        )

        all_files: List[Tuple[str, str]] = []
        for td in tel_dirs:
            if self._stop:
                break
            self.log.emit(f"  Scanning /{td}…")
            for item in self._walk(ftp, f"/{td}", depth=0):
                all_files.append(item)

        total = len(all_files)
        self.log.emit(f"Found {total} matching file(s) total.")
        self.overall.emit(0, total)

        downloaded = 0
        skipped = 0

        for idx, (remote_dir, filename) in enumerate(all_files, start=1):
            if self._stop:
                self.log.emit("Stopped by user.")
                break

            remote_path = f"{remote_dir}/{filename}"
            fit_name = filename.removesuffix(".zip")

            rel_subdir = remote_dir.lstrip("/")
            local_dir = os.path.join(self.download_path, rel_subdir)
            local_fit = os.path.join(local_dir, fit_name)
            local_zip = os.path.join(local_dir, filename)

            if os.path.exists(local_fit):
                self.log.emit(f"  SKIP   {remote_path}")
                skipped += 1
                self.overall.emit(idx, total)
                continue

            os.makedirs(local_dir, exist_ok=True)

            try:
                file_size = ftp.size(remote_path) or 0
            except Exception:
                file_size = 0

            self.log.emit(
                f"  DL     {remote_path}  ({file_size / 1_048_576:.1f} MB)"
            )
            self.file_started.emit(remote_path, file_size)

            # Download via RETR with progress callback
            _progress = [0]   # mutable container avoids nonlocal complexity

            try:
                with open(local_zip, "wb") as fh:
                    def _write(data: bytes) -> None:
                        fh.write(data)
                        _progress[0] += len(data)
                        self.file_bytes.emit(
                            _progress[0],
                            file_size if file_size else _progress[0],
                        )

                    ftp.retrbinary(f"RETR {remote_path}", _write)
            except Exception as exc:
                self.log.emit(f"  ERROR  downloading {remote_path}: {exc}")
                if os.path.exists(local_zip):
                    try:
                        os.remove(local_zip)
                    except OSError:
                        pass
                self.overall.emit(idx, total)
                continue

            # Unzip
            try:
                with zipfile.ZipFile(local_zip, "r") as zf:
                    zf.extractall(local_dir)
                os.remove(local_zip)
                self.log.emit(f"  UNZIP  {fit_name}")
            except Exception as exc:
                self.log.emit(f"  ERROR  unzipping {local_zip}: {exc}")
                self.overall.emit(idx, total)
                continue

            if self.delete_after:
                try:
                    ftp.delete(remote_path)
                    self.log.emit(f"  DEL    {remote_path} (removed from server)")
                except Exception as exc:
                    self.log.emit(
                        f"  WARN   Could not delete {remote_path} from server: {exc}"
                    )

            downloaded += 1
            self.overall.emit(idx, total)

        self.log.emit(
            f"\nFinished — Downloaded: {downloaded}  |  Skipped: {skipped}"
        )
        self.finished.emit(downloaded, skipped)

    def _walk(self, ftp, remote_dir: str, depth: int) -> Iterator[Tuple[str, str]]:
        """Recursively yield (remote_dir, filename) for matching files."""
        if depth > _MAX_DEPTH:
            return

        # Try MLSD first — gives type info without extra round-trips
        try:
            entries = list(ftp.mlsd(remote_dir, facts=["type"]))
            for name, facts in entries:
                if name in (".", ".."):
                    continue
                child = f"{remote_dir}/{name}"
                etype = facts.get("type", "").lower()
                if etype == "dir":
                    yield from self._walk(ftp, child, depth + 1)
                elif etype in ("file", "") and fnmatch.fnmatch(name, _FILE_GLOB):
                    yield remote_dir, name
            return
        except (ftplib.error_perm, ftplib.error_proto, AttributeError):
            pass  # server doesn't support MLSD — fall through to LIST

        # Fallback: parse LIST output (unix-style "drwx..." / "-rw..." lines).
        # This correctly identifies subdirectories so we can recurse into them,
        # which is essential for the T??/<Object>/<date>/ layout.
        list_lines: list = []
        try:
            ftp.retrlines(f"LIST {remote_dir}", list_lines.append)
        except Exception:
            return

        for line in list_lines:
            # Typical unix line: "drwxr-xr-x  2 user grp 4096 Jan 1 12:00 name"
            # Windows/IIS line:  "01-01-24  12:00AM       <DIR>          name"
            parts = line.split(None, 8)
            if len(parts) < 2:
                continue

            # Detect directory: unix permission string starts with 'd',
            # or Windows style contains '<DIR>'.
            is_dir = parts[0].startswith("d") or "<DIR>" in line.upper()
            name = parts[-1].strip()
            if not name or name in (".", ".."):
                continue

            child = f"{remote_dir}/{name}"
            if is_dir:
                yield from self._walk(ftp, child, depth + 1)
            elif fnmatch.fnmatch(name, _FILE_GLOB):
                yield remote_dir, name
