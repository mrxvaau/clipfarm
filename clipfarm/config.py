import os
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_env(path=None):
    """Tiny .env loader (no extra dependency). Real environment variables win."""
    path = Path(path) if path else ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config(path=None):
    load_env()
    path = Path(path) if path else ROOT / "config.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg.setdefault("poll_minutes", 15)
    cfg.setdefault("lookback_hours", 24)
    cfg.setdefault("db_path", "clips.db")
    cfg.setdefault("dashboard_port", 5000)
    cfg.setdefault("paths", {})
    cfg["paths"].setdefault("work_dir", "work")
    cfg["paths"].setdefault("output_dir", "output")
    cfg.setdefault("tools", {})
    for k, v in {"ffmpeg": "ffmpeg", "ytdlp": "yt-dlp", "whisper_cli": "",
                 "whisper_model": "", "whisper_language": "auto"}.items():
        cfg["tools"].setdefault(k, v)
    cfg.setdefault("render", {})
    for k, v in {"video_encoder": "libx264", "caption_font_size": 64,
                 "caption_margin_v": 300}.items():
        cfg["render"].setdefault(k, v)
    cfg["_root"] = str(ROOT)
    return cfg


def load_streamers(path=None):
    path = Path(path) if path else ROOT / "streamers.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = []
    for s in data.get("streamers", []) or []:
        login = str(s["login"]).strip().lower()
        perm = str(s.get("permission", "pending")).strip().lower()
        if perm not in ("confirmed", "pending"):
            perm = "pending"
        out.append({"login": login, "platform": s.get("platform", "twitch"), "permission": perm})
    return out


def resolve_path(cfg, p):
    p = Path(p)
    return p if p.is_absolute() else Path(cfg["_root"]) / p
