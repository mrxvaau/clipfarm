import argparse
import logging
import os
import shutil
import sys

from . import db
from .config import load_config, load_streamers, resolve_path


def _client():
    from .twitch import TwitchClient
    return TwitchClient(os.environ.get("TWITCH_CLIENT_ID"), os.environ.get("TWITCH_CLIENT_SECRET"))


def cmd_check(cfg, a):
    t = cfg["tools"]
    ok = True

    def line(name, good, extra=""):
        nonlocal ok
        print(f"[{'OK ' if good else 'MISSING'}] {name} {extra}")
        ok = ok and good

    line("ffmpeg", bool(shutil.which(t["ffmpeg"])), t["ffmpeg"])
    line("yt-dlp", bool(shutil.which(t["ytdlp"])), t["ytdlp"])
    line("TWITCH_CLIENT_ID/SECRET", bool(os.environ.get("TWITCH_CLIENT_ID") and os.environ.get("TWITCH_CLIENT_SECRET")))
    wc, wm = t.get("whisper_cli"), t.get("whisper_model")
    if wc and wm:
        line("whisper-cli", bool(shutil.which(wc)) or os.path.exists(wc), wc)
        line("whisper model", os.path.exists(wm), wm)
    else:
        print("[skip] whisper not configured -> clips render without captions")
    s = load_streamers()
    print(f"streamers.yaml: {len(s)} streamers, {sum(x['permission']=='confirmed' for x in s)} confirmed")
    return 0 if ok else 1


def cmd_poll(cfg, a):
    from .poller import poll_once
    conn = db.connect(resolve_path(cfg, cfg["db_path"]))
    print(poll_once(cfg, _client(), conn))


def cmd_run(cfg, a):
    from .poller import run_loop
    conn = db.connect(resolve_path(cfg, cfg["db_path"]))
    run_loop(cfg, _client(), conn)


def cmd_top(cfg, a):
    conn = db.connect(resolve_path(cfg, cfg["db_path"]))
    perm = None if a.permission == "any" else a.permission
    for r in db.top_clips(conn, a.n, a.status, perm):
        print(f"{r['score']:>9}  {r['view_count']:>7}v  {r['broadcaster_name']:<18} {r['permission']:<9} {r['id']}  {r['title'][:50]}")


def cmd_set_status(cfg, a):
    conn = db.connect(resolve_path(cfg, cfg["db_path"]))
    print("ok" if db.set_status(conn, a.clip_id, a.status) else "clip not found")


def cmd_dashboard(cfg, a):
    from .dashboard import create_app
    create_app(cfg).run(host="127.0.0.1", port=cfg["dashboard_port"])


def cmd_process(cfg, a):
    from .process import parse_time, process_clip, process_shortlisted
    conn = db.connect(resolve_path(cfg, cfg["db_path"]))
    if a.all:
        done, failed = process_shortlisted(cfg, conn)
        print("done:", *done, sep="\n  ")
        print("failed:", *[f"{i}: {e}" for i, e in failed], sep="\n  ")
    else:
        print(process_clip(cfg, conn, a.clip_id, start=parse_time(a.start), end=parse_time(a.end),
                           mode=a.mode, crop_x=a.crop_x))


def main(argv=None):
    p = argparse.ArgumentParser(prog="clipfarm")
    p.add_argument("--config")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    sub.add_parser("poll").set_defaults(fn=cmd_poll)
    sub.add_parser("run").set_defaults(fn=cmd_run)
    t = sub.add_parser("top"); t.set_defaults(fn=cmd_top)
    t.add_argument("--n", type=int, default=20)
    t.add_argument("--status", default="new")
    t.add_argument("--permission", default="confirmed", choices=["confirmed", "pending", "unknown", "any"])
    s = sub.add_parser("set-status"); s.set_defaults(fn=cmd_set_status)
    s.add_argument("clip_id"); s.add_argument("status", choices=db.STATUSES)
    sub.add_parser("dashboard").set_defaults(fn=cmd_dashboard)
    pr = sub.add_parser("process"); pr.set_defaults(fn=cmd_process)
    g = pr.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="process every shortlisted confirmed clip")
    g.add_argument("--clip-id")
    pr.add_argument("--start", help="cut start, seconds or m:ss (e.g. 12.5 or 0:12.5)")
    pr.add_argument("--end", help="cut end, seconds or m:ss")
    pr.add_argument("--mode", choices=["blur", "fill"], help="blur = whole clip over blurred bg; fill = crop a 9:16 slice")
    pr.add_argument("--crop-x", type=float, dest="crop_x", help="fill mode slice position: 0=left, 0.5=center, 1=right")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(a.config)
    return a.fn(cfg, a) or 0


if __name__ == "__main__":
    sys.exit(main())
