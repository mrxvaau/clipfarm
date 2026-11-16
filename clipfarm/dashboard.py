import threading
from datetime import datetime, timezone

from flask import Flask, redirect, render_template_string, request, url_for

from . import db
from .config import load_config
from .process import process_clip
from .scoring import hours_between

TEMPLATE = """
<!doctype html><meta charset="utf-8"><title>clipfarm</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{font-family:system-ui,sans-serif;background:#111;color:#eee;margin:0;padding:16px}
 a{color:#8ab4ff} .tabs a{margin-right:14px;text-decoration:none;padding:6px 10px;border-radius:6px;background:#222}
 .tabs a.on{background:#3b5bdb;color:#fff}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;margin-top:16px}
 .card{background:#1b1b1b;border-radius:10px;overflow:hidden;border:1px solid #2a2a2a}
 .card img{width:100%;display:block;aspect-ratio:16/9;object-fit:cover}
 .pad{padding:10px} .t{font-weight:600;margin-bottom:4px} .m{color:#aaa;font-size:13px}
 button{padding:6px 10px;border:0;border-radius:6px;cursor:pointer;margin:6px 6px 0 0;color:#fff}
 .ok{background:#2f9e44}.no{background:#c92a2a}.go{background:#3b5bdb}
 .badge{font-size:11px;padding:2px 6px;border-radius:4px;background:#444;margin-left:6px}
 .badge.confirmed{background:#2f9e44}.warn{background:#5c3b00;padding:8px;border-radius:6px;margin-top:10px}
</style>
<h2>clipfarm</h2>
<div class="tabs">
 {% for t in tabs %}<a class="{{ 'on' if t==tab else '' }}" href="{{ url_for('index', tab=t, scout=1 if scout else None) }}">{{ t }}</a>{% endfor %}
 <a href="{{ url_for('index', tab=tab, scout=None if scout else 1) }}">{{ 'hide pending' if scout else 'scouting view' }}</a>
</div>
{% if scout %}<div class="warn">Scouting view: includes streamers WITHOUT confirmed permission. You can look, but cannot shortlist or download them.</div>{% endif %}
<div class="grid">
{% for c in clips %}
 <div class="card">
  <a href="{{ c.url }}" target="_blank"><img src="{{ c.thumbnail_url }}" loading="lazy"></a>
  <div class="pad">
   <div class="t">{{ c.title }}</div>
   <div class="m">{{ c.broadcaster_name }}<span class="badge {{ c.permission }}">{{ c.permission }}</span></div>
   <div class="m">{{ c.view_count }} views · score {{ c.score }} · {{ c.age }}h old · {{ c.duration|round(0)|int }}s</div>
   {% if c.out_path %}<div class="m">saved: {{ c.out_path }}</div>{% endif %}
   <form method="post" style="display:inline">
    {% if c.permission == 'confirmed' %}
     {% if c.status in ('new','rejected') %}<button class="ok" formaction="{{ url_for('set_status', clip_id=c.id, status='shortlisted') }}">Shortlist</button>{% endif %}
     {% if c.status == 'shortlisted' %}<button class="go" formaction="{{ url_for('process', clip_id=c.id) }}">Download + render</button>{% endif %}
     {% if c.status in ('new','shortlisted') %}<button class="no" formaction="{{ url_for('set_status', clip_id=c.id, status='rejected') }}">Reject</button>{% endif %}
     {% if c.status == 'downloaded' %}<button class="ok" formaction="{{ url_for('set_status', clip_id=c.id, status='posted') }}">Mark posted</button>{% endif %}
    {% endif %}
    <input type="hidden" name="tab" value="{{ tab }}"><input type="hidden" name="scout" value="{{ 1 if scout else '' }}">
   </form>
  </div>
 </div>
{% else %}<p>Nothing here yet. Run <code>python -m clipfarm poll</code>.</p>{% endfor %}
</div>
"""

TABS = ["new", "shortlisted", "downloaded", "rejected", "posted"]


def create_app(cfg=None, conn=None):
    cfg = cfg or load_config()
    from .config import resolve_path
    conn = conn or db.connect(resolve_path(cfg, cfg["db_path"]))
    app = Flask(__name__)
    app.config["CF"] = cfg
    app.config["CONN"] = conn

    def back():
        return redirect(url_for("index", tab=request.form.get("tab", "new"),
                                scout=1 if request.form.get("scout") else None))

    @app.get("/")
    def index():
        tab = request.args.get("tab", "new")
        if tab not in TABS:
            tab = "new"
        scout = bool(request.args.get("scout"))
        rows = db.top_clips(conn, n=60, status=tab, permission=None if scout else "confirmed")
        now = datetime.now(timezone.utc)
        clips = []
        for r in rows:
            d = dict(r)
            d["age"] = round(hours_between(r["created_at"], now), 1)
            d["thumbnail_url"] = d.get("thumbnail_url") or ""
            d["duration"] = d.get("duration") or 0
            clips.append(type("C", (), d))
        return render_template_string(TEMPLATE, clips=clips, tabs=TABS, tab=tab, scout=scout)

    @app.post("/set/<clip_id>/<status>")
    def set_status(clip_id, status):
        clip = db.get_clip(conn, clip_id)
        if clip is not None and clip["permission"] == "confirmed" and status in db.STATUSES:
            db.set_status(conn, clip_id, status)
        return back()

    @app.post("/process/<clip_id>")
    def process(clip_id):
        clip = db.get_clip(conn, clip_id)
        if clip is not None and clip["permission"] == "confirmed" and clip["status"] == "shortlisted":
            def work():
                c2 = db.connect(resolve_path(cfg, cfg["db_path"]))
                try:
                    process_clip(cfg, c2, clip_id)
                except Exception as e:
                    print(f"[process {clip_id}] failed: {e}")
            threading.Thread(target=work, daemon=True).start()
        return back()

    return app
