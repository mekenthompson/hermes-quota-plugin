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


def sanitize_snapshot(snapshot):
    """Allowlisted quota-only exchange format, never credential/raw payload data."""
    if not isinstance(snapshot, dict) or not _stamp(snapshot.get("fetched_at")):
        raise ValueError("snapshot requires a timezone-aware fetch timestamp")
    providers = snapshot.get("providers")
    if not isinstance(providers, dict) or len(providers) > 64:
        raise ValueError("snapshot requires at most 64 provider records")

    def record(raw):
        if not isinstance(raw, dict):
            raise ValueError("snapshot provider must be a record")
        reason = raw.get("unavailable_reason")
        clean = {"label": _text(raw.get("label"), "account", 80),
                 "provider": _text(raw.get("provider"), "", 80),
                 "account_label": _text(raw.get("account_label"), "", 80),
                 "plan": _text(raw.get("plan"), "", 32),
                 "unavailable_reason": _text(reason, "fetch-error", 80) if reason else None,
                 "windows": [], "accounts": []}
        hint = raw.get("history_id")
        if isinstance(hint, str) and re.fullmatch(r"[a-f0-9]{64}", hint):
            clean["history_id"] = hint
        windows = raw.get("windows") or []
        accounts = raw.get("accounts") or []
        if not isinstance(windows, list) or len(windows) > 16 or not isinstance(accounts, list) or len(accounts) > 8:
            raise ValueError("snapshot windows/accounts exceed exchange bounds")
        if not reason:
            for window in windows:
                if not isinstance(window, dict):
                    raise ValueError("snapshot window must be a record")
                used = window.get("used_percent")
                if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used) or not 0 <= used <= 100:
                    used = None
                clean["windows"].append({"label": _text(window.get("label"), "unknown", 80),
                                         "used_percent": used, "reset_at": _stamp(window.get("reset_at"))})
        for account in accounts:
            if not isinstance(account, dict) or account.get("accounts"):
                raise ValueError("nested accounts are not supported")
            clean["accounts"].append(record(account))
        return clean

    out = {}
    for key, value in providers.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9][a-z0-9:._-]{0,79}", key):
            raise ValueError("invalid snapshot provider key")
        out[key] = record(value)
    return {"fetched_at": _stamp(snapshot["fetched_at"]), "providers": out}


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


def forecasts(rows, now=None):
    """Linear recent observed pace per account/window, never summed allowances.

    Three continuous observations over at least two hours are required. Rates
    use at most the last 24 hours. Any missing observation, failure, reset or
    decrease starts a new segment. These are conditional estimates, not limits
    promised by the vendor; zero measured burn is not proof of future capacity.
    """
    stamp = _stamp(now) if now is not None else datetime.now(timezone.utc).isoformat()
    if stamp is None:
        raise ValueError("forecast now requires a timezone-aware timestamp")
    clock = datetime.fromisoformat(stamp)
    ordered = sorted(rows, key=lambda r: r["fetched_at"])
    segments, latest = {}, {}
    sweeps = {}
    for row in ordered:
        sweeps.setdefault(row["fetched_at"], []).append(row)
    for fetched, sweep in sweeps.items():
        present = {(r["provider"], r["account"], r["window"]) for r in sweep}
        segments = {key: seg for key, seg in segments.items() if key in present}
        for row in sweep:
            key = (row["provider"], row["account"], row["window"])
            latest[key] = row
            valid = (row["status"] == "ok" and row["identity_verified"]
                     and bool(_stamp(row["reset_at"]))
                     and isinstance(row["used_percent"], (int, float))
                     and not isinstance(row["used_percent"], bool)
                     and math.isfinite(row["used_percent"])
                     and 0 <= row["used_percent"] <= 100)
            if not valid:
                segments.pop(key, None)
                # An unidentified error cannot establish which prior account
                # failed, so none of this provider's old segments stay valid.
                if not row["identity_verified"]:
                    segments = {k: v for k, v in segments.items() if k[0] != row["provider"]}
                continue
            segment = segments.get(key, [])
            if segment:
                previous = segment[-1]
                elapsed = datetime.fromisoformat(fetched) - datetime.fromisoformat(previous["fetched_at"])
                if (row["reset_at"] != previous["reset_at"] or row["used_percent"] < previous["used_percent"]
                        or not timedelta(0) < elapsed <= timedelta(hours=2)):
                    segment = []
            segment.append(row)
            cutoff = datetime.fromisoformat(fetched) - timedelta(hours=24)
            segments[key] = [r for r in segment if datetime.fromisoformat(r["fetched_at"]) >= cutoff]
    newest = ordered[-1]["fetched_at"] if ordered else None
    output = []
    # Unverified observations have deliberately unique storage identities. Only
    # the newest display row is useful; this is presentation deduplication, not
    # permission to join those identities into a rate series.
    unknown_latest = {}
    for key, last in latest.items():
        if not last["identity_verified"]:
            unknown_latest[(key[0], last["label"], key[2])] = last["fetched_at"]
    for key, last in sorted(latest.items()):
        if (not last["identity_verified"] and
                last["fetched_at"] != unknown_latest[(key[0], last["label"], key[2])]):
            continue
        used = last["used_percent"] if last["status"] == "ok" else None
        result = {"provider": key[0], "account": key[1], "label": last["label"], "window": key[2],
                  "reset_at": last["reset_at"] or None, "fetched_at": last["fetched_at"],
                  "used_percent": used, "remaining_percent": 100 - used if used is not None else None,
                  "status": "insufficient-data", "rate_percentage_points_per_hour": None,
                  "projected_used_percent_at_reset": None, "estimated_exhaustion_at": None,
                  "sample_count": 0, "observed_hours": 0, "method": "recent-linear-pace",
                  "scope": "shared-account"}
        age = clock - datetime.fromisoformat(last["fetched_at"])
        reset = _stamp(last["reset_at"])
        segment = segments.get(key, [])
        if last["fetched_at"] != newest:
            result["status"] = "missing-latest-observation"
        elif age < timedelta(0):
            result["status"] = "future-observation"
        elif age > timedelta(hours=2):
            result["status"] = "stale"
        elif last["status"] != "ok":
            result["status"] = "unavailable"
        elif not last["identity_verified"]:
            result["status"] = "unknown-account"
        elif not reset:
            result["status"] = "missing-reset"
        elif datetime.fromisoformat(reset) <= clock:
            result["status"] = "reset-due"
        elif used is not None and used >= 100:
            result["status"] = "exhausted"
        elif len(segment) >= 3:
            hours = (datetime.fromisoformat(segment[-1]["fetched_at"]) - datetime.fromisoformat(segment[0]["fetched_at"])).total_seconds() / 3600
            result.update(sample_count=len(segment), observed_hours=hours)
            if hours >= 2:
                rate = (segment[-1]["used_percent"] - segment[0]["used_percent"]) / hours
                result["rate_percentage_points_per_hour"] = rate
                if rate <= 0:
                    result["status"] = "no-observed-burn"
                else:
                    left = (datetime.fromisoformat(reset) - datetime.fromisoformat(last["fetched_at"])).total_seconds() / 3600
                    result["projected_used_percent_at_reset"] = used + rate * left
                    exhaustion = datetime.fromisoformat(last["fetched_at"]) + timedelta(hours=(100 - used) / rate)
                    result["estimated_exhaustion_at"] = exhaustion.isoformat()
                    result["status"] = "exhausts-before-reset" if exhaustion < datetime.fromisoformat(reset) else "within-quota-at-observed-pace"
        output.append(result)
    return output


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
