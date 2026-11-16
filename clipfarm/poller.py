import logging
import time
from datetime import datetime, timedelta, timezone

from . import db
from .config import load_streamers
from .scoring import iso

log = logging.getLogger("clipfarm")


def poll_once(cfg, client, conn, streamers=None, now=None):
    """One discovery pass. Returns dict of counts."""
    streamers = streamers if streamers is not None else load_streamers()
    now = now or datetime.now(timezone.utc)
    now_iso = iso(now)
    start = iso(now - timedelta(hours=cfg["lookback_hours"]))

    users = client.get_users([s["login"] for s in streamers]) if streamers else {}
    perm_by_id, perm_by_name, ids = {}, {}, []
    for s in streamers:
        u = users.get(s["login"])
        perm_by_name[s["login"]] = s["permission"]
        if u:
            perm_by_id[u["id"]] = s["permission"]
            perm_by_name[u["display_name"].lower()] = s["permission"]
            ids.append(u["id"])
        else:
            log.warning("Streamer not found on Twitch: %s", s["login"])

    seen = {}
    game_id = None
    if cfg.get("game_name"):
        game_id = client.get_game_id(cfg["game_name"])
        if game_id:
            for c in client.get_clips(start, now_iso, game_id=game_id):
                seen[c["id"]] = c
        else:
            log.warning("Game category %r not found yet - using streamer list only", cfg["game_name"])
    for bid in ids:
        # streamers' own clips of any game; keep only the target game when it exists
        for c in client.get_clips(start, now_iso, broadcaster_id=bid):
            if game_id and c.get("game_id") != game_id:
                continue
            seen[c["id"]] = c

    counts = {"new": 0, "updated": 0, "game_id": game_id}
    for c in seen.values():
        perm = perm_by_id.get(c.get("broadcaster_id")) or \
            perm_by_name.get((c.get("broadcaster_name") or "").lower()) or "unknown"
        counts[db.upsert_clip(conn, c, perm, now_iso)] += 1
    counts["permissions_refreshed"] = db.refresh_permissions(conn, perm_by_id, perm_by_name)
    return counts


def run_loop(cfg, client, conn):
    while True:
        try:
            log.info("poll: %s", poll_once(cfg, client, conn))
        except Exception as e:  # keep the loop alive on network hiccups
            log.error("poll failed: %s", e)
        time.sleep(cfg["poll_minutes"] * 60)
