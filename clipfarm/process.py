"""Download -> exact trim -> vertical 9:16 -> captions.

Only for clips from CONFIRMED-permission streamers. No AI involved: every cut and crop
is deterministic FFmpeg with numbers you choose.
"""
import logging
import re
import shutil
import subprocess
from pathlib import Path

from . import db
from .config import resolve_path

log = logging.getLogger("clipfarm")

MODES = ("blur", "fill")


def run(cmd, cwd=None):
    cmd = [str(c) for c in cmd]
    log.info("$ %s", " ".join(cmd))
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} failed ({r.returncode}):\n{r.stderr[-1500:]}")
    return r


def parse_time(value):
    """'12.5' | '1:05.5' | '0:01:05' -> seconds (float). Blank/None -> None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    v = str(value).strip()
    if not v:
        return None
    parts = v.split(":")
    if len(parts) > 3:
        raise ValueError(f"bad time: {value!r}")
    try:
        secs = 0.0
        for p in parts:
            secs = secs * 60 + float(p)
    except ValueError:
        raise ValueError(f"bad time: {value!r}")
    if secs < 0:
        raise ValueError("time can't be negative")
    return secs


def validate_edit(start, end, mode, crop_x):
    if start is not None and end is not None and end <= start:
        raise ValueError("end must be after start")
    if mode is not None and mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if crop_x is not None and not 0.0 <= crop_x <= 1.0:
        raise ValueError("crop_x must be between 0 and 1 (0=left, 0.5=center, 1=right)")


def build_filter(has_subs, font_size=64, margin_v=300, mode="blur", crop_x=0.5):
    """1080x1920 output.

    blur: whole clip centered over a blurred copy of itself (nothing is cropped away).
    fill: crop a 9:16 slice out of the clip; crop_x picks the slice (0=left .. 1=right).
    """
    if mode == "fill":
        video = f"[0:v]scale=-2:1920,crop=1080:1920:x='(iw-1080)*{float(crop_x):.4f}':y=0"
    else:
        video = (
            "[0:v]split=2[bg][fg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bgb];"
            "[fg]scale=1080:-2[fgs];"
            "[bgb][fgs]overlay=(W-w)/2:(H-h)/2"
        )
    if has_subs:
        style = f"Alignment=2,FontSize={font_size},Outline=4,Shadow=0,Bold=1,MarginV={margin_v}"
        return f"{video}[v];[v]subtitles=subs.srt:original_size=1080x1920:force_style='{style}'[out]"
    return f"{video}[out]"


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9_-]+", "", s or "")[:40] or "clip"


def trim(cfg, workdir, src_name, start, end, out_name="trimmed.mp4"):
    """Frame-accurate cut (re-encodes, so the cut lands exactly where you say, not on a keyframe)."""
    cmd = [cfg["tools"]["ffmpeg"], "-y", "-i", src_name]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-t", f"{end - (start or 0):.3f}"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", out_name]
    run(cmd, cwd=workdir)
    return out_name


def render(cfg, workdir, src_name, out_path, has_subs, mode="blur", crop_x=0.5):
    r = cfg["render"]
    cmd = [
        cfg["tools"]["ffmpeg"], "-y", "-i", src_name,
        "-filter_complex", build_filter(has_subs, r["caption_font_size"], r["caption_margin_v"], mode, crop_x),
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


def process_clip(cfg, conn, clip_id, start=None, end=None, mode=None, crop_x=None):
    """Explicit arguments win; otherwise use the values saved for this clip; otherwise config defaults."""
    clip = db.get_clip(conn, clip_id)
    if clip is None:
        raise ValueError(f"unknown clip id {clip_id}")
    if clip["permission"] != "confirmed":
        raise PermissionError(
            f"{clip['broadcaster_name']} is not marked 'confirmed' in streamers.yaml - refusing to download")

    start = start if start is not None else clip["trim_start"]
    end = end if end is not None else clip["trim_end"]
    mode = mode or clip["fit_mode"] or cfg["render"]["mode"]
    crop_x = crop_x if crop_x is not None else (clip["crop_x"] if clip["crop_x"] is not None else cfg["render"]["crop_x"])
    validate_edit(start, end, mode, crop_x)

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

    # 2) exact cut (only when a start/end was chosen); captions are made from the cut version
    if start or end is not None:
        src = trim(cfg, workdir, src, start, end)

    # 3) captions (optional)
    has_subs = transcribe(cfg, workdir, src)

    # 4) render
    base = f"{safe_name(clip['broadcaster_name'])}_{safe_name(clip_id)}"
    out_path = outdir / f"{base}.mp4"
    render(cfg, workdir, src, out_path, has_subs, mode, crop_x)

    # 5) credit/notes file next to the video
    (outdir / f"{base}.txt").write_text(
        f"Clip title: {clip['title']}\n"
        f"Streamer: {clip['broadcaster_name']}\n"
        f"Source: {clip['url']}\n"
        f"Cut: start={start or 0}s end={end if end is not None else 'full'} mode={mode} crop_x={crop_x}\n\n"
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
