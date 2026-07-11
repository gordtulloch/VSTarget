"""AAVSO Target Tool API client and background fetch thread."""
from __future__ import annotations

from typing import List, Optional

import requests
from PySide6.QtCore import QThread, Signal

from models import AAVSOTarget

BASE_URL = "https://targettool.aavso.org/TargetTool/api/v1"


class AAVSOClient:
    """Synchronous client for the AAVSO Target Tool REST API (v1).

    Authentication uses HTTP Basic Auth with the API key as the username
    and the literal string ``api_token`` as the password.
    """

    def __init__(self, api_key: str) -> None:
        self._auth = (api_key, "api_token")

    def get_targets(
        self,
        sections: Optional[List[str]] = None,
        observable: bool = False,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        target_altitude: Optional[float] = None,
        sun_altitude: Optional[float] = None,
    ) -> List[AAVSOTarget]:
        """Fetch variable star targets from the API.

        Parameters
        ----------
        sections:
            List of section codes (e.g. ``["ac", "cv"]``).  Defaults to
            Alerts & Campaigns (``["ac"]``) when not provided.
        observable:
            When ``True`` only targets visible at the given location during
            the next nighttime period are returned.
        latitude / longitude / target_altitude / sun_altitude:
            Override the telescope location stored in the user's account.
        """
        params: dict = {"obs_section": sections or ["ac"]}
        if observable:
            params["observable"] = "true"
        if latitude is not None:
            params["latitude"] = latitude
        if longitude is not None:
            params["longitude"] = longitude
        if target_altitude is not None:
            params["targetaltitude"] = target_altitude
        if sun_altitude is not None:
            params["sunaltitude"] = sun_altitude

        resp = requests.get(
            f"{BASE_URL}/targets",
            auth=self._auth,
            params=params,
            timeout=60,
        )
        resp.raise_for_status()
        return [AAVSOTarget.from_api(t) for t in resp.json().get("targets", [])]


class FetchTargetsThread(QThread):
    """Background thread that fetches AAVSO targets without blocking the UI."""

    # Emitted on success with the list of targets
    finished = Signal(list)
    # Emitted on failure with a human-readable error message
    error = Signal(str)
    # Short status messages for the status bar
    status = Signal(str)

    def __init__(
        self,
        api_key: str,
        sections: List[str],
        observable: bool,
        latitude: float,
        longitude: float,
        target_altitude: float,
        sun_altitude: float,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._api_key = api_key
        self._sections = sections
        self._observable = observable
        self._latitude = latitude
        self._longitude = longitude
        self._target_altitude = target_altitude
        self._sun_altitude = sun_altitude

    def run(self) -> None:
        self.status.emit("Connecting to AAVSO Target Tool…")
        try:
            client = AAVSOClient(self._api_key)
            targets = client.get_targets(
                sections=self._sections,
                observable=self._observable,
                latitude=self._latitude,
                longitude=self._longitude,
                target_altitude=self._target_altitude,
                sun_altitude=self._sun_altitude,
            )
            self.status.emit(f"Downloaded {len(targets)} targets.")
            self.finished.emit(targets)

        except requests.exceptions.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else "?"
            if code == 401:
                self.error.emit(
                    "Authentication failed (HTTP 401).\n"
                    "Please check your API key in Settings."
                )
            elif code == 429:
                self.error.emit(
                    "Rate limit exceeded (HTTP 429).\n"
                    "You have made too many requests.  Please try again later."
                )
            elif code == 410:
                self.error.emit(
                    "API no longer in service (HTTP 410).\n"
                    "Please check https://targettool.aavso.org for updates."
                )
            else:
                self.error.emit(f"HTTP error {code}: {exc}")

        except requests.exceptions.ConnectionError:
            self.error.emit(
                "Connection failed.\n"
                "Please check your internet connection."
            )
        except requests.exceptions.Timeout:
            self.error.emit("Request timed out.  Please try again.")
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"Unexpected error: {exc}")
