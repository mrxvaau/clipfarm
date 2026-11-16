"""Download -> vertical 9:16 -> captions. Only for clips from CONFIRMED-permission streamers."""
import logging
import re
import shutil
import subprocess
from pathlib import Path

from . import db
from .config import resolve_path

log = logging.getLogger("clipfarm")


def run(cmd, cwd=None):
    cmd = [str(c) for c in cmd]
    log.info("$ %s", " ".join(cmd))
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} failed ({r.returncode}):\n{r.stderr[-1500:]}")
    return r


def build_filter(has_subs, font_size=64, margin_v=300):
    """Blurred-background 1080x1920 canvas with the clip centered; optional burned-in captions."""
    f = (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bgb];"
        "[fg]scale=1080:-2[fgs];"
        "[bgb][fgs]overlay=(W-w)/2:(H-h)/2"
    )
    if has_subs:
        style = f"Alignment=2,FontSize={font_size},Outline=4,Shadow=0,Bold=1,MarginV={margin_v}"
        f += f"[v];[v]subtitles=subs.srt:original_size=1080x1920:force_style='{style}'[out]"
    else:
        f += "[out]"
    return f


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9_-]+", "", s or "")[:40] or "clip"


def render(cfg, workdir, src_name, out_path, has_subs):
    r = cfg["render"]
    cmd = [
        cfg["tools"]["ffmpeg"], "-y", "-i", src_name,
        "-filter_complex", build_filter(has_subs, r["caption_font_size"], r["caption_margin_v"]),
        "-map", "[out]", "-map", "0:a?",
        "-c:v", r["video_encoder"], "-pix_fmt", "yuv420p",
    ]
    if r["video_encoder"] == "libx264":
        cmd += ["-preset", "veryfast", "-crf", "20"]
    cmd += ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out_path)]
    # cwd=workdir + relative subs.srt avoids Windows drive-letter escaping problems in the subtitles filter
    run(cmd, cwd=workdir)


def transcribe(cfg, workdir, src_name):
    """Return True if subs.srt was produced. Skips silently when whisper isn't configured."""
    t = cfg["tools"]
    if not t.get("whisper_cli") or not t.get("whisper_model"):
        log.warning("whisper not configured - rendering without captions")
        return False
    run([t["ffmpeg"], "-y", "-i", src_name, "-ar", "16000", "-ac", "1",
         "-c:a", "pcm_s16le", "audio.wav"], cwd=workdir)
    run([t["whisper_cli"], "-m", t["whisper_model"], "-f", "audio.wav",
         "-osrt", "-of", "subs", "-l", t.get("whisper_language", "auto")], cwd=workdir)
    srt = Path(workdir) / "subs.srt"
    return srt.exists() and srt.stat().st_size > 0


def process_clip(cfg, conn, clip_id):
    clip = db.get_clip(conn, clip_id)
    if clip is None:
        raise ValueError(f"unknown clip id {clip_id}")
    if clip["permission"] != "confirmed":
        raise PermissionError(
            f"{clip['broadcaster_name']} is not marked 'confirmed' in streamers.yaml - refusing to download")
    workdir = resolve_path(cfg, cfg["paths"]["work_dir"]) / safe_name(clip_id)
    outdir = resolve_path(cfg, cfg["paths"]["output_dir"])
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)

    # 1) download
    run([cfg["tools"]["ytdlp"], "-o", "source.%(ext)s", "--no-playlist", clip["url"]], cwd=workdir)
    sources = [p for p in workdir.glob("source.*")]
    if not sources:
        raise RuntimeError("download produced no file")
    src = sources[0].name

    # 2) captions (optional)
    has_subs = transcribe(cfg, workdir, src)

    # 3) render
    base = f"{safe_name(clip['broadcaster_name'])}_{safe_name(clip_id)}"
    out_path = outdir / f"{base}.mp4"
    render(cfg, workdir, src, out_path, has_subs)

    # 4) credit/notes file next to the video
    (outdir / f"{base}.txt").write_text(
        f"Clip title: {clip['title']}\n"
        f"Streamer: {clip['broadcaster_name']}\n"
        f"Source: {clip['url']}\n\n"
        f"Description credit line:\n"
        f"Clip from {clip['broadcaster_name']} - {clip['url']}\n\n"
        f"TODO before posting: add your own Bangla hook/commentary, then upload.\n",
        encoding="utf-8")

    conn.execute("UPDATE clips SET status='downloaded', out_path=? WHERE id=?", (str(out_path), clip_id))
    conn.commit()
    shutil.rmtree(workdir, ignore_errors=True)
    return out_path


def process_shortlisted(cfg, conn):
    done, failed = [], []
    for c in conn.execute(
            "SELECT id FROM clips WHERE status='shortlisted' AND permission='confirmed' ORDER BY score DESC").fetchall():
        try:
            done.append(str(process_clip(cfg, conn, c["id"])))
        except Exception as e:
            log.error("process %s failed: %s", c["id"], e)
            failed.append((c["id"], str(e)))
    return done, failed
