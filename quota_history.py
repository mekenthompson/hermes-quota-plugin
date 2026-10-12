"""Sanitized profile-local history. Quota percentages are not token usage.

Only stable, explicitly supplied history identities are joinable over time.
Unidentified observations are retained but never silently combined as accounts.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

RETENTION_DAYS = 90
_SECRET = re.compile(r"bearer\s|sk-|eyJ[A-Za-z0-9_-]{20,}", re.I)


def _path():
    from hermes_constants import get_hermes_home
    return Path(get_hermes_home()) / "quota_history.sqlite3"


def _text(value, fallback="", limit=120):
    if not isinstance(value, str) or _SECRET.search(value):
        return fallback
    return " ".join(value.split())[:limit]


def _stamp(value):
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _connect(path):
    db = sqlite3.connect(str(path), timeout=15)
    db.execute("PRAGMA busy_timeout=15000")
    db.execute("""CREATE TABLE IF NOT EXISTS samples (
        fetched_at TEXT NOT NULL, provider TEXT NOT NULL, account TEXT NOT NULL,
        label TEXT NOT NULL, window TEXT NOT NULL, reset_at TEXT NOT NULL,
        used_percent REAL, status TEXT NOT NULL, identity_verified INTEGER NOT NULL,
        PRIMARY KEY(fetched_at, provider, account, window, reset_at))""")
    return db


def record_snapshot(snapshot):
    """Commit one observation atomically; retrying identical observations is a no-op.

    Allowlisted fields only: no tokens, vendor raw payloads, or detail strings.
    Storage failure propagates so a collector cannot claim a durable sample.
    """
    stamp = _stamp(snapshot.get("fetched_at"))
    if stamp is None:
        raise ValueError("quota history requires a timezone-aware fetch timestamp")
    providers = snapshot.get("providers")
    if not isinstance(providers, dict):
        raise ValueError("quota history requires a provider mapping")
    rows = []
    for key, record in providers.items():
        if not isinstance(record, dict):
            continue
        provider = _text(record.get("provider") or key, "unknown", 80)
        nested = record.get("accounts")
        records = nested if key == "openai-codex" and isinstance(nested, list) and nested else [record]
        for index, rec in enumerate(records):
            if not isinstance(rec, dict):
                continue
            hint = rec.get("history_id")
            verified = isinstance(hint, str) and re.fullmatch(r"[a-f0-9]{64}", hint) is not None
            account = hint if verified else f"unverified:{stamp}:{_text(key, 'unknown', 80)}:{index}"
            label = _text(rec.get("account_label") or rec.get("label"), provider)
            raw_reason = rec.get("unavailable_reason")
            failed = raw_reason is not None and raw_reason != ""
            reason = _text(raw_reason, "fetch-error") if failed else ""
            windows = rec.get("windows")
            windows = windows if isinstance(windows, list) and windows and not reason else [{}]
            for window in windows:
                if not isinstance(window, dict):
                    continue
                used = window.get("used_percent")
                if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used) or not 0 <= used <= 100:
                    used = None
                status = reason or ("ok" if used is not None else "no-window-data")
                rows.append((stamp, provider, account, label, _text(window.get("label"), "unknown"),
                             _stamp(window.get("reset_at")) or "", used, status, int(verified)))
    path = _path()
    if path.is_symlink():
        raise ValueError("quota history path must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    db = _connect(path)
    try:
        with db:
            db.executemany("INSERT OR IGNORE INTO samples VALUES (?,?,?,?,?,?,?,?,?)", rows)
            newest = db.execute("SELECT MAX(fetched_at) FROM samples").fetchone()[0]
            if newest:
                cutoff = (datetime.fromisoformat(newest) - timedelta(days=RETENTION_DAYS)).isoformat()
                db.execute("DELETE FROM samples WHERE fetched_at < ?", (cutoff,))
    finally:
        db.close()
    path.chmod(0o600)
    return len(rows)


def read_history(days=30, provider=None):
    """Read-only bounded history; absence is not invented historical data."""
    days = int(days)
    if not 1 <= days <= RETENTION_DAYS:
        raise ValueError("history days must be between 1 and 90")
    path = _path()
    if path.is_symlink():
        raise ValueError("quota history path must not be a symlink")
    if not path.exists():
        return []
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
    db.row_factory = sqlite3.Row
    try:
        newest = db.execute("SELECT MAX(fetched_at) FROM samples").fetchone()[0]
        if not newest:
            return []
        cutoff = (datetime.fromisoformat(newest) - timedelta(days=days)).isoformat()
        sql = "SELECT * FROM samples WHERE fetched_at >= ?"
        args = [cutoff]
        if provider:
            sql += " AND provider = ?"
            args.append(provider)
        return [dict(row) for row in db.execute(sql + " ORDER BY fetched_at,provider,account,window LIMIT 20000", args)]
    finally:
        db.close()


def daily_changes(rows):
    """Observed deltas, not API daily totals. Never bridge gaps or resets."""
    previous, totals = {}, {}
    rows = sorted(rows, key=lambda row: (row["fetched_at"], row["provider"], row["account"], row["window"]))
    present = {}
    for row in rows:
        present.setdefault(row["fetched_at"], set()).add((row["provider"], row["account"], row["window"]))
    last_stamp = None
    for row in rows:
        if row["fetched_at"] != last_stamp:
            # A partial snapshot is evidence of a gap for each absent window.
            # Keep continuity only for windows actually observed this sweep.
            previous = {key: value for key, value in previous.items()
                        if key in present[row["fetched_at"]]}
            last_stamp = row["fetched_at"]
        if row["status"] != "ok":
            for old in list(previous):
                if old[0] == row["provider"] and (not row["identity_verified"] or old[1] == row["account"]):
                    del previous[old]
            continue
        key = (row["provider"], row["account"], row["window"])
        last = previous.get(key)
        previous[key] = row
        if not row["identity_verified"] or not row["reset_at"] or row["status"] != "ok" or not last or last["status"] != "ok" or row["reset_at"] != last["reset_at"]:
            continue
        elapsed = datetime.fromisoformat(row["fetched_at"]) - datetime.fromisoformat(last["fetched_at"])
        delta = row["used_percent"] - last["used_percent"]
        if not timedelta(0) < elapsed <= timedelta(hours=2) or delta < 0:
            continue
        day = row["fetched_at"][:10]
        bucket = (day,) + key + (row["reset_at"],)
        totals[bucket] = totals.get(bucket, 0.0) + delta
    return [{"day_utc": key[0], "provider": key[1], "account": key[2], "window": key[3],
             "reset_at": key[4], "observed_percentage_points": value}
            for key, value in sorted(totals.items())]
