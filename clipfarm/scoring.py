from datetime import datetime, timezone


def parse_ts(s):
    """Parse Twitch ISO timestamps like 2026-11-19T10:00:00Z into aware UTC datetimes."""
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hours_between(a, b):
    return max((parse_ts(b) - parse_ts(a)).total_seconds() / 3600.0, 0.0)


def compute_score(view_count, view_count_first, created_at, first_seen_at, now):
    """Views per hour since creation + growth per hour since we first saw the clip.

    Min age of 0.5h avoids brand-new clips with a few views exploding the score.
    """
    age = max(hours_between(created_at, now), 0.5)
    base = view_count / age
    seen = max(hours_between(first_seen_at, now), 0.5)
    growth = max(view_count - (view_count_first or 0), 0) / seen
    return round(base + growth, 2)
