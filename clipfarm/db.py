import sqlite3
from .scoring import compute_score

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
  id TEXT PRIMARY KEY,
  url TEXT, title TEXT,
  broadcaster_id TEXT, broadcaster_name TEXT,
  game_id TEXT, thumbnail_url TEXT,
  view_count INTEGER, duration REAL, created_at TEXT,
  first_seen_at TEXT, last_seen_at TEXT, view_count_first INTEGER,
  score REAL DEFAULT 0,
  permission TEXT DEFAULT 'unknown',
  status TEXT DEFAULT 'new',
  out_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_clips_status_score ON clips(status, score DESC);
"""

STATUSES = ("new", "shortlisted", "rejected", "downloaded", "posted")


def connect(path):
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_clip(conn, clip, permission, now_iso):
    """Insert or update a clip from a Twitch API object. Returns 'new' or 'updated'."""
    row = conn.execute(
        "SELECT view_count_first, first_seen_at FROM clips WHERE id=?", (clip["id"],)
    ).fetchone()
    views = int(clip.get("view_count", 0))
    if row is None:
        score = compute_score(views, views, clip["created_at"], now_iso, now_iso)
        conn.execute(
            """INSERT INTO clips (id,url,title,broadcaster_id,broadcaster_name,game_id,
               thumbnail_url,view_count,duration,created_at,first_seen_at,last_seen_at,
               view_count_first,score,permission,status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'new')""",
            (clip["id"], clip.get("url"), clip.get("title"), clip.get("broadcaster_id"),
             clip.get("broadcaster_name"), clip.get("game_id"), clip.get("thumbnail_url"),
             views, clip.get("duration"), clip["created_at"], now_iso, now_iso,
             views, score, permission),
        )
        conn.commit()
        return "new"
    score = compute_score(views, row["view_count_first"], clip["created_at"],
                          row["first_seen_at"], now_iso)
    conn.execute(
        "UPDATE clips SET view_count=?, last_seen_at=?, title=?, score=?, permission=? WHERE id=?",
        (views, now_iso, clip.get("title"), score, permission, clip["id"]),
    )
    conn.commit()
    return "updated"


def top_clips(conn, n=20, status="new", permission="confirmed"):
    q = "SELECT * FROM clips WHERE status=?"
    args = [status]
    if permission:
        q += " AND permission=?"
        args.append(permission)
    q += " ORDER BY score DESC LIMIT ?"
    args.append(n)
    return conn.execute(q, args).fetchall()


def set_status(conn, clip_id, status):
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    cur = conn.execute("UPDATE clips SET status=? WHERE id=?", (status, clip_id))
    conn.commit()
    return cur.rowcount > 0


def get_clip(conn, clip_id):
    return conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()


def refresh_permissions(conn, perm_by_id, perm_by_name):
    """Re-apply streamers.yaml permissions to clips already stored (when you flip pending->confirmed)."""
    rows = conn.execute("SELECT id, broadcaster_id, broadcaster_name FROM clips").fetchall()
    changed = 0
    for r in rows:
        perm = perm_by_id.get(r["broadcaster_id"]) or perm_by_name.get((r["broadcaster_name"] or "").lower()) or "unknown"
        cur = conn.execute("UPDATE clips SET permission=? WHERE id=? AND permission<>?", (perm, r["id"], perm))
        changed += cur.rowcount
    conn.commit()
    return changed
