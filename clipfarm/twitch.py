import time
import requests

API = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"


class TwitchError(RuntimeError):
    pass


class TwitchClient:
    def __init__(self, client_id, client_secret, session=None):
        if not client_id or not client_secret:
            raise TwitchError("Set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET (see .env.example)")
        self.client_id = client_id
        self.client_secret = client_secret
        self.s = session or requests.Session()
        self._token = None
        self._expires = 0.0

    # ---- auth ----
    def _refresh_token(self):
        r = self.s.post(TOKEN_URL, params={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "grant_type": "client_credentials"}, timeout=20)
        if r.status_code != 200:
            raise TwitchError(f"Token request failed: {r.status_code} {r.text[:200]}")
        data = r.json()
        self._token = data["access_token"]
        self._expires = time.time() + int(data.get("expires_in", 3600)) - 120

    def _headers(self):
        if not self._token or time.time() >= self._expires:
            self._refresh_token()
        return {"Client-Id": self.client_id, "Authorization": f"Bearer {self._token}"}

    # ---- low level ----
    def _get(self, path, params=None, _retried=False):
        r = self.s.get(f"{API}{path}", params=params, headers=self._headers(), timeout=20)
        if r.status_code == 401 and not _retried:
            self._token = None
            return self._get(path, params, _retried=True)
        if r.status_code == 429:
            reset = float(r.headers.get("Ratelimit-Reset", time.time() + 5))
            time.sleep(max(min(reset - time.time(), 30), 1))
            return self._get(path, params, _retried=True)
        if r.status_code != 200:
            raise TwitchError(f"GET {path} -> {r.status_code}: {r.text[:200]}")
        if int(r.headers.get("Ratelimit-Remaining", 100)) < 3:
            time.sleep(1.5)
        return r.json()

    # ---- endpoints ----
    def get_game_id(self, name):
        data = self._get("/games", {"name": name}).get("data", [])
        return data[0]["id"] if data else None

    def get_users(self, logins):
        """Return {login: {"id":..., "display_name":...}} (batches of 100)."""
        out = {}
        logins = list(dict.fromkeys(l.lower() for l in logins))
        for i in range(0, len(logins), 100):
            batch = logins[i:i + 100]
            data = self._get("/users", [("login", l) for l in batch]).get("data", [])
            for u in data:
                out[u["login"].lower()] = {"id": u["id"], "display_name": u["display_name"]}
        return out

    def get_clips(self, started_at, ended_at, game_id=None, broadcaster_id=None, pages=3):
        if not game_id and not broadcaster_id:
            raise ValueError("need game_id or broadcaster_id")
        params = {"first": 100, "started_at": started_at, "ended_at": ended_at}
        if game_id:
            params["game_id"] = game_id
        else:
            params["broadcaster_id"] = broadcaster_id
        clips, cursor = [], None
        for _ in range(pages):
            if cursor:
                params["after"] = cursor
            data = self._get("/clips", params)
            clips.extend(data.get("data", []))
            cursor = (data.get("pagination") or {}).get("cursor")
            if not cursor:
                break
        return clips
