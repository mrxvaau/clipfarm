# clipfarm (local, free)

Finds the best-performing Twitch clips from your streamer list, lets you shortlist them in a
local dashboard, then downloads, converts to 9:16 and burns in captions. You add your own
Bangla commentary in CapCut/DaVinci, then upload.

Only streamers marked `confirmed` in `streamers.yaml` can be shortlisted or downloaded.

## 1. Install (Windows, PowerShell)
```
winget install Gyan.FFmpeg
winget install yt-dlp.yt-dlp
cd clipfarm
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # then put your Twitch Client ID/Secret in .env
```
Twitch keys: https://dev.twitch.tv/console -> Register Your Application
(redirect URL http://localhost, category Application Integration).

## 2. Captions (optional but recommended)
Build whisper.cpp (Vulkan works on AMD cards; CPU build is fine too), download `ggml-small.bin`,
then set `tools.whisper_cli` and `tools.whisper_model` in `config.yaml`.
Leave them empty to render without captions.

## 3. Check everything
```
python -m clipfarm check
```

## 4. Daily use
```
python -m clipfarm poll          # one discovery pass
python -m clipfarm run           # keep polling every 15 min (leave this window open)
python -m clipfarm dashboard     # open http://127.0.0.1:5000
python -m clipfarm top --n 20    # same list in the terminal
python -m clipfarm process --all # render every shortlisted clip
```
Finished videos land in `output/` with a `.txt` credit file next to each.

## Notes
- Before launch the GTA 6 category may not exist; the app then uses your streamer list only
  (their clips of any game).
- Edit `streamers.yaml`, set `permission: confirmed` only after a streamer says yes.
  The next poll re-applies permissions to clips already stored.
- Honor takedown requests immediately. Add your own commentary to every clip.
- AMD encoding: try `render.video_encoder: h264_amf` in config.yaml (Windows) if renders are slow.
- Run tests: `pip install pytest` then `pytest`.

First run: copy streamers.example.yaml to streamers.yaml and edit it.
