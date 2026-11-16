import os
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from clipfarm import db
from clipfarm.config import load_config
from clipfarm.poller import poll_once
from clipfarm.process import build_filter, process_clip
from clipfarm.scoring import compute_score, iso

NOW = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)


def clip(i, views=100, mins_old=60, bid="1", bname="Kai", game="g1"):
    return {"id": i, "url": f"https://clips.twitch.tv/{i}", "title": f"clip {i}",
            "broadcaster_id": bid, "broadcaster_name": bname, "game_id": game,
            "thumbnail_url": "http://x/t.jpg", "view_count": views, "duration": 30.0,
            "created_at": iso(NOW - timedelta(minutes=mins_old))}


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "t.db")


@pytest.fixture
def cfg(tmp_path):
    c = load_config()
    c["_root"] = str(tmp_path)
    c["db_path"] = "t.db"
    return c


# ---------- scoring ----------
def test_score_views_per_hour():
    s = compute_score(200, 200, iso(NOW - timedelta(hours=2)), iso(NOW), NOW)
    assert s == 100.0


def test_score_min_age_and_growth():
    # 10 views, 6 minutes old -> age floored to 0.5h => 20/h
    assert compute_score(10, 10, iso(NOW - timedelta(minutes=6)), iso(NOW), NOW) == 20.0
    # seen 1h ago with 100, now 300 -> growth 200/h added
    first_seen = iso(NOW - timedelta(hours=1))
    s = compute_score(300, 100, iso(NOW - timedelta(hours=2)), first_seen, NOW)
    assert s == 150.0 + 200.0


# ---------- db ----------
def test_upsert_new_then_update(conn):
    assert db.upsert_clip(conn, clip("a", 100), "confirmed", iso(NOW)) == "new"
    later = NOW + timedelta(hours=1)
    assert db.upsert_clip(conn, clip("a", 400), "confirmed", iso(later)) == "updated"
    r = db.get_clip(conn, "a")
    assert r["view_count"] == 400 and r["view_count_first"] == 100
    assert r["first_seen_at"] == iso(NOW)


def test_status_and_top_filter(conn):
    db.upsert_clip(conn, clip("a", 500), "confirmed", iso(NOW))
    db.upsert_clip(conn, clip("b", 900), "pending", iso(NOW))
    assert [r["id"] for r in db.top_clips(conn, 10)] == ["a"]
    assert {r["id"] for r in db.top_clips(conn, 10, permission=None)} == {"a", "b"}
    assert db.set_status(conn, "a", "shortlisted")
    assert db.top_clips(conn, 10) == []
    with pytest.raises(ValueError):
        db.set_status(conn, "a", "bogus")


# ---------- poller with a fake Twitch ----------
class FakeTwitch:
    def get_game_id(self, name):
        return "g1"

    def get_users(self, logins):
        m = {"kai": ("1", "Kai"), "ghost": None}
        return {l: {"id": m[l][0], "display_name": m[l][1]} for l in logins if m.get(l)}

    def get_clips(self, started_at, ended_at, game_id=None, broadcaster_id=None, pages=3):
        if game_id:
            return [clip("game1", 800, bid="1", bname="Kai"), clip("game2", 50, bid="9", bname="Random")]
        return [clip("own1", 300, bid="1", bname="Kai"), clip("other_game", 999, bid="1", bname="Kai", game="zzz")]


def test_poll_once_permissions(cfg, conn):
    streamers = [{"login": "kai", "platform": "twitch", "permission": "confirmed"},
                 {"login": "ghost", "platform": "twitch", "permission": "pending"}]
    counts = poll_once(cfg, FakeTwitch(), conn, streamers=streamers, now=NOW)
    assert counts["new"] == 3  # game1, game2, own1 (other_game filtered out)
    perms = {r["id"]: r["permission"] for r in conn.execute("SELECT id, permission FROM clips")}
    assert perms == {"game1": "confirmed", "game2": "unknown", "own1": "confirmed"}
    # flipping pending->confirmed later re-applies to stored clips
    streamers[0]["permission"] = "pending"
    poll_once(cfg, FakeTwitch(), conn, streamers=streamers, now=NOW)
    assert db.get_clip(conn, "game1")["permission"] == "pending"


# ---------- dashboard ----------
def test_dashboard_gate_and_pages(cfg, conn):
    from clipfarm.dashboard import create_app
    db.upsert_clip(conn, clip("ok", 500), "confirmed", iso(NOW))
    db.upsert_clip(conn, clip("nope", 900, bname="Pending"), "pending", iso(NOW))
    c = create_app(cfg, conn).test_client()
    html = c.get("/").get_data(as_text=True)
    assert "clip ok" in html and "clip nope" not in html
    html = c.get("/?scout=1").get_data(as_text=True)
    assert "clip nope" in html and "Scouting view" in html
    assert c.post("/set/ok/shortlisted").status_code == 302
    assert db.get_clip(conn, "ok")["status"] == "shortlisted"
    c.post("/set/nope/shortlisted")  # must be ignored: not confirmed
    assert db.get_clip(conn, "nope")["status"] == "new"
    assert "Download + render" in c.get("/?tab=shortlisted").get_data(as_text=True)


# ---------- processor ----------
def test_process_refuses_unconfirmed(cfg, conn):
    db.upsert_clip(conn, clip("p", 500), "pending", iso(NOW))
    with pytest.raises(PermissionError):
        process_clip(cfg, conn, "p")


def test_filter_strings():
    assert "subtitles=subs.srt" in build_filter(True)
    assert "subtitles" not in build_filter(False)


def _make_test_video(path):
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
                   check=True, capture_output=True)


def _fake_tool(path, body):
    path.write_text("#!" + sys.executable + "\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_process_end_to_end_with_fake_downloader_and_whisper(cfg, conn, tmp_path):
    src = tmp_path / "sample.mp4"
    _make_test_video(src)
    ytdlp = tmp_path / "fake_ytdlp"
    _fake_tool(ytdlp, f"import shutil\nshutil.copy(r'{src}', 'source.mp4')\n")
    whisper = tmp_path / "fake_whisper"
    _fake_tool(whisper, "open('subs.srt','w').write('1\\n00:00:00,000 --> 00:00:02,000\\nHello clip test\\n\\n')\n")
    model = tmp_path / "model.bin"; model.write_bytes(b"x")
    cfg["tools"].update({"ytdlp": str(ytdlp), "whisper_cli": str(whisper), "whisper_model": str(model)})
    db.upsert_clip(conn, clip("c1", 500), "confirmed", iso(NOW))
    out = process_clip(cfg, conn, "c1")
    assert out.exists() and out.stat().st_size > 1000
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0", str(out)], capture_output=True, text=True)
    assert probe.stdout.strip() == "1080,1920"
    assert out.with_suffix(".txt").exists()
    assert db.get_clip(conn, "c1")["status"] == "downloaded"


def test_process_without_whisper_still_renders(cfg, conn, tmp_path):
    src = tmp_path / "sample.mp4"
    _make_test_video(src)
    ytdlp = tmp_path / "fake_ytdlp"
    _fake_tool(ytdlp, f"import shutil\nshutil.copy(r'{src}', 'source.mp4')\n")
    cfg["tools"].update({"ytdlp": str(ytdlp), "whisper_cli": "", "whisper_model": ""})
    db.upsert_clip(conn, clip("c2", 500), "confirmed", iso(NOW))
    assert process_clip(cfg, conn, "c2").exists()
