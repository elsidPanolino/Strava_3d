"""Minimal Strava API client: OAuth (with local callback), token refresh, activities, streams."""

from __future__ import annotations

import json
import os
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
API_BASE = "https://www.strava.com/api/v3"
DEFAULT_SCOPE = "read,activity:read_all"
DEFAULT_PORT = 8765


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "strava3d"


def default_token_path() -> Path:
    return config_dir() / "token.json"


def load_dotenv(path: Path = Path(".env")) -> None:
    """Load KEY=VALUE lines from a .env file into os.environ (without overriding)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Credentials:
    client_id: str
    client_secret: str

    @classmethod
    def from_env(cls) -> "Credentials":
        load_dotenv()
        cid = os.environ.get("STRAVA_CLIENT_ID")
        secret = os.environ.get("STRAVA_CLIENT_SECRET")
        if not cid or not secret:
            raise SystemExit(
                "Missing STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET. Create an API app at "
                "https://www.strava.com/settings/api and put them in a .env file "
                "(see .env.example)."
            )
        return cls(cid, secret)


class StravaError(RuntimeError):
    pass


class StravaClient:
    def __init__(self, creds: Credentials, token_path: Path | None = None, session=None):
        self.creds = creds
        self.token_path = token_path or default_token_path()
        self.session = session or requests.Session()
        self._token: dict | None = None

    # ---- OAuth -----------------------------------------------------------

    def authorize_url(self, redirect_uri: str, scope: str = DEFAULT_SCOPE) -> str:
        params = {
            "client_id": self.creds.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "approval_prompt": "auto",
            "scope": scope,
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> dict:
        resp = self.session.post(
            TOKEN_URL,
            data={
                "client_id": self.creds.client_id,
                "client_secret": self.creds.client_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise StravaError(f"Token exchange failed ({resp.status_code}): {resp.text}")
        token = resp.json()
        self._save_token(token)
        return token

    def login(self, port: int = DEFAULT_PORT, open_browser: bool = True) -> dict:
        """Run the OAuth flow using a temporary local HTTP server to catch the redirect."""
        redirect_uri = f"http://localhost:{port}/callback"
        result: dict = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 (http.server API)
                query = parse_qs(urlparse(self.path).query)
                if "code" in query:
                    result["code"] = query["code"][0]
                    result["scope"] = query.get("scope", [""])[0]
                    body = b"<h2>Strava connected. You can close this tab.</h2>"
                else:
                    result["error"] = query.get("error", ["unknown"])[0]
                    body = b"<h2>Authorization failed. Check the terminal.</h2>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(("localhost", port), Handler)
        url = self.authorize_url(redirect_uri)
        print(f"Open this URL to authorize Strava access:\n\n  {url}\n")
        if open_browser:
            threading.Timer(0.5, webbrowser.open, args=(url,)).start()
        while not result:
            server.handle_request()
        server.server_close()

        if "error" in result:
            raise StravaError(f"Authorization denied: {result['error']}")
        if "activity:read" not in result.get("scope", ""):
            print("Warning: activity read scope was not granted; private activities may be missing.")
        return self.exchange_code(result["code"])

    # ---- Token storage / refresh ----------------------------------------

    def _save_token(self, token: dict) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(token, indent=2))
        try:
            self.token_path.chmod(0o600)
        except OSError:
            pass
        self._token = token

    def _load_token(self) -> dict:
        if self._token is None:
            if not self.token_path.exists():
                raise StravaError("Not logged in. Run `strava3d auth` first.")
            self._token = json.loads(self.token_path.read_text())
        return self._token

    def access_token(self) -> str:
        token = self._load_token()
        if token.get("expires_at", 0) - 60 < time.time():
            resp = self.session.post(
                TOKEN_URL,
                data={
                    "client_id": self.creds.client_id,
                    "client_secret": self.creds.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": token["refresh_token"],
                },
                timeout=30,
            )
            if resp.status_code != 200:
                raise StravaError(f"Token refresh failed ({resp.status_code}): {resp.text}")
            refreshed = resp.json()
            token = {**token, **refreshed}
            self._save_token(token)
        return token["access_token"]

    # ---- API calls -------------------------------------------------------

    def _get(self, path: str, params: dict | None = None):
        resp = self.session.get(
            f"{API_BASE}{path}",
            headers={"Authorization": f"Bearer {self.access_token()}"},
            params=params,
            timeout=30,
        )
        if resp.status_code == 429:
            raise StravaError("Strava rate limit hit; wait 15 minutes and try again.")
        if resp.status_code != 200:
            raise StravaError(f"GET {path} failed ({resp.status_code}): {resp.text}")
        return resp.json()

    def athlete(self) -> dict:
        return self._get("/athlete")

    def activities(self, per_page: int = 30, page: int = 1) -> list[dict]:
        return self._get("/athlete/activities", {"per_page": per_page, "page": page})

    def activity(self, activity_id: int) -> dict:
        return self._get(f"/activities/{activity_id}")

    def activity_latlng(self, activity_id: int) -> list[tuple[float, float]]:
        """Full-resolution GPS track of an activity as [(lat, lon), ...]."""
        streams = self._get(
            f"/activities/{activity_id}/streams",
            {"keys": "latlng", "key_by_type": "true"},
        )
        latlng = streams.get("latlng", {}).get("data") if isinstance(streams, dict) else None
        if latlng:
            return [tuple(p) for p in latlng]
        # Fall back to the summary polyline (e.g. if streams are unavailable).
        from .geo import decode_polyline

        poly = self.activity(activity_id).get("map", {}).get("summary_polyline")
        if not poly:
            raise StravaError(f"Activity {activity_id} has no GPS data.")
        return decode_polyline(poly)
