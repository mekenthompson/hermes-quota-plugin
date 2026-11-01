"""Public Codex special-reset announcement (announcement metadata only).

The endpoint at https://codex-resets.com is a third-party aggregator of public
Codex reset announcements. This module deliberately ignores provider
credentials, account quota, ``reset_at`` values and banked-reset entitlement:
it reports only an explicitly announced global reset, with its publication
time. An announcement never means the reset was applied to a given account.

Transport rules: fixed HTTPS endpoint, TLS verification, no redirects, no
environment proxies, no cookies, no Authorization header, bounded response
size and a short timeout.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import ssl
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

ENDPOINT = "https://codex-resets.com/api/v1/status"
API_VERSION = "v1"
MAX_BODY_BYTES = 64 * 1024
TIMEOUT_S = 5.0
# The documented response currently carries ``max-age=14400``. Cap an absurd
# header value so one bad origin response cannot pin a stale view for days.
MAX_CACHE_S = 24 * 60 * 60
DEFAULT_CACHE_S = 4 * 60 * 60
MAX_RETRY_AFTER_S = 24 * 60 * 60
STATE_FILENAME = "quota_special_reset_state.json"

_ALLOWED_AUTHOR = "thsottiaux"
_X_POST_URL = re.compile(r"^https://x\.com/thsottiaux/status/[0-9]+$")
_CONTROL_CATEGORIES = {"Cc", "Cf", "Cs", "Zl", "Zp"}

logger = logging.getLogger(__name__)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None  # urllib raises HTTPError; refresh maps it to "redirect"


class SpecialResetError(Exception):
    """A bounded, machine-readable fetch/parse failure."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _clean_text(value: Any, limit: int) -> Optional[str]:
    """A short control-free string, or None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > limit:
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return None
    import unicodedata

    if any(unicodedata.category(ch) in _CONTROL_CATEGORIES for ch in value):
        return None
    return value


def _iso_utc(value: Any) -> Optional[str]:
    """Normalize an aware ISO-8601 timestamp to UTC; None when invalid."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None
        return stamp.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def _announcement(value: Any, status: str) -> Optional[dict[str, Any]]:
    """Validate one explicit announcement record. Observed-only is rejected."""
    if not isinstance(value, dict):
        return None
    reset_id = _clean_text(value.get("id"), 64)
    reset_type = value.get("reset_type")
    announced_at = _iso_utc(value.get("announced_at"))
    # The publisher's own estimate of when the reset lands. It is announcement
    # metadata, not evidence that the reset ran; status stays "scheduled".
    scheduled_for = _iso_utc(value.get("scheduled_for")) if status == "scheduled" else None
    source = value.get("source")
    if not reset_id or reset_type not in ("regular", "banked") or announced_at is None:
        return None
    if not isinstance(source, dict) or source.get("type") != "x_post":
        return None
    if source.get("author") != _ALLOWED_AUTHOR:
        return None
    source_url = source.get("url")
    if not isinstance(source_url, str) or not _X_POST_URL.fullmatch(source_url):
        return None
    return {
        "id": reset_id,
        "status": status,
        "reset_type": reset_type,
        "announced_at": announced_at,
        "scheduled_for": scheduled_for,
        "source_url": source_url,
    }


def select_announcement(payload: Any) -> Optional[dict[str, Any]]:
    """Pick a valid explicit announcement, scheduled first, else latest.

    ``text``, forecasts and statistics are intentionally ignored. The
    publisher's ``scheduled_for`` is kept as an announced estimate; it never
    proves that a reset ran, so the record stays ``scheduled``.
    """
    if not isinstance(payload, dict):
        raise SpecialResetError("schema")
    data = payload.get("data")
    meta = payload.get("meta")
    if not isinstance(data, dict) or not isinstance(meta, dict):
        raise SpecialResetError("schema")
    if meta.get("api_version") != API_VERSION:
        raise SpecialResetError("schema")

    scheduled = data.get("scheduled_reset")
    chosen = None
    if isinstance(scheduled, dict) and scheduled.get("status") == "scheduled":
        chosen = _announcement(scheduled, "scheduled")
    if chosen is None:
        chosen = _announcement(data.get("latest_reset"), "recorded")
    if chosen is None:
        # Observations are deliberately outside this feature; malformed
        # explicit announcements are a failure, not a fresh empty result.
        for value in (scheduled, data.get("latest_reset")):
            if value is None:
                continue
            source = value.get("source") if isinstance(value, dict) else None
            if isinstance(source, dict) and source.get("type") == "observed":
                continue
            raise SpecialResetError("schema")
    return chosen


def empty_entry(reason: str = "not-fetched") -> dict[str, Any]:
    return {
        "announcement": None,
        "fetched_at": None,
        "stale": True,
        "unavailable_reason": reason,
    }


def normalize_entry(value: Any) -> dict[str, Any]:
    """Coerce cached/foreign data to the exact public contract."""
    if not isinstance(value, dict):
        return empty_entry()
    raw_announcement = value.get("announcement")
    announcement = raw_announcement
    normalized_announcement = None
    if isinstance(announcement, dict):
        reset_id = _clean_text(announcement.get("id"), 64)
        status = announcement.get("status")
        reset_type = announcement.get("reset_type")
        announced_at = _iso_utc(announcement.get("announced_at"))
        source_url = announcement.get("source_url")
        if (reset_id and status in ("scheduled", "recorded")
                and reset_type in ("regular", "banked")
                and announced_at is not None
                and isinstance(source_url, str)
                and _X_POST_URL.fullmatch(source_url)):
            normalized_announcement = {
                "id": reset_id,
                "status": status,
                "reset_type": reset_type,
                "announced_at": announced_at,
                # An announced estimate only; status still carries the state.
                "scheduled_for": _iso_utc(announcement.get("scheduled_for"))
                if status == "scheduled" else None,
                "source_url": source_url,
            }
    fetched_at = _iso_utc(value.get("fetched_at"))
    reason = value.get("unavailable_reason")
    announcement_rejected = (raw_announcement is not None
                             and normalized_announcement is None)
    freshness_unknown = (normalized_announcement is not None
                         and fetched_at is None)
    normalized_reason = (
        _clean_text(reason, 80) if isinstance(reason, str) else None
    )
    if normalized_reason is None:
        if announcement_rejected:
            normalized_reason = "schema"
        elif freshness_unknown:
            normalized_reason = "stale"
        elif normalized_announcement is None and fetched_at is None:
            normalized_reason = "not-fetched"
    return {
        "announcement": normalized_announcement,
        "fetched_at": fetched_at,
        "stale": (value.get("stale") is not False
                  or announcement_rejected or freshness_unknown),
        "unavailable_reason": normalized_reason,
    }


def _state_path(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    from hermes_constants import get_hermes_home

    return os.path.join(str(get_hermes_home()), STATE_FILENAME)


def _representation_digest(entry: dict[str, Any]) -> str:
    """Bind validators to the exact normalized entry they describe."""
    canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read_state(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            value = json.load(fh)
    except (OSError, ValueError, UnicodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    state: dict[str, Any] = {}
    etag = value.get("etag")
    if isinstance(etag, str) and 0 < len(etag) <= 256 and "\n" not in etag:
        state["etag"] = etag
    digest = value.get("entry_digest")
    if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest):
        state["entry_digest"] = digest
    for key in ("expires_at", "retry_after_until"):
        stamp_value = value.get(key)
        if isinstance(stamp_value, (int, float)) and not isinstance(stamp_value, bool):
            state[key] = float(stamp_value)
    return state


def _write_state(path: str, state: dict[str, Any]) -> None:
    tmp_path = None
    try:
        directory = os.path.dirname(os.path.abspath(path))
        fd, tmp_path = tempfile.mkstemp(
            prefix="." + os.path.basename(path) + ".", suffix=".tmp",
            dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
        tmp_path = None
    except OSError:
        logger.debug("codex_special_reset ▸ state write failed", exc_info=True)
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _headers_map(headers: Any) -> dict[str, str]:
    try:
        items = headers.items()
    except AttributeError:
        return {}
    return {str(key).lower(): str(value) for key, value in items}


def _max_age(headers: dict[str, str]) -> float:
    cache_control = headers.get("cache-control", "")
    lowered = cache_control.lower()
    if "no-store" in lowered or "no-cache" in lowered:
        return 0.0
    match = re.search(r"(?:^|,)\s*max-age\s*=\s*(\d+)", lowered)
    if not match:
        return float(DEFAULT_CACHE_S)
    return float(min(int(match.group(1)), MAX_CACHE_S))


def _retry_after_seconds(headers: dict[str, str], now: float) -> float:
    raw = headers.get("retry-after", "").strip()
    if not raw:
        return 0.0
    if raw.isdigit():
        return float(min(int(raw), MAX_RETRY_AFTER_S))
    try:
        from email.utils import parsedate_to_datetime

        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    try:
        wait = when.timestamp() - now
    except (OverflowError, OSError, ValueError):
        return 0.0
    return float(min(max(0.0, wait), MAX_RETRY_AFTER_S))


def _default_transport(url: str, timeout: float,
                       etag: Optional[str]) -> tuple[int, Any, bytes]:
    """One bounded, anonymous, non-redirecting HTTPS GET."""
    opener = urllib.request.build_opener(
        _NoRedirect(), urllib.request.ProxyHandler({})
    )
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "User-Agent": "hermes-quota-plugin",
    }
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        code = int(getattr(exc, "code", 0) or 0)
        if code in (301, 302, 303, 307, 308):
            try:
                exc.close()
            except Exception:  # pragma: no cover - old-Python cleanup
                pass
            raise SpecialResetError("redirect")
        try:
            body = exc.read(MAX_BODY_BYTES + 1)
            response_headers = exc.headers
        finally:
            try:
                exc.close()
            except Exception:  # pragma: no cover - old-Python cleanup
                pass
        return code, response_headers, body
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (TimeoutError, ssl.SSLError)):
            raise SpecialResetError(
                "tls-error" if isinstance(reason, ssl.SSLError) else "timeout"
            )
        raise SpecialResetError("network-error")
    except ssl.SSLError:
        raise SpecialResetError("tls-error")
    except TimeoutError:
        raise SpecialResetError("timeout")
    try:
        body = response.read(MAX_BODY_BYTES + 1)
        status = int(getattr(response, "status", 200) or 200)
        response_headers = response.headers
    finally:
        try:
            response.close()
        except Exception:  # pragma: no cover - cleanup best effort
            pass
    if len(body) > MAX_BODY_BYTES:
        raise SpecialResetError("oversize")
    if status == 200:
        media_type = _headers_map(response_headers).get(
            "content-type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/json" and not media_type.endswith("+json"):
            raise SpecialResetError("content-type")
    return status, response_headers, body


def stale_entry(previous: Any, reason: str) -> dict[str, Any]:
    """Keep the last valid announcement, marked stale with a real reason."""
    entry = normalize_entry(previous)
    entry["stale"] = True
    entry["unavailable_reason"] = reason
    return entry


def refresh(previous: Any = None, *, transport=None, now=None,
            state_path: Optional[str] = None,
            timeout: float = TIMEOUT_S) -> dict[str, Any]:
    """Refresh the announcement cache. Never raises; never invents a reset.

    A network call is skipped while the origin's Cache-Control window is
    open, or during a Retry-After cooldown. Failures preserve the last valid
    announcement, mark it stale, and record a machine-readable reason.
    """
    clock = now or time.time
    try:
        current = float(clock())
    except Exception:
        current = time.time()
    prior = normalize_entry(previous)
    path = _state_path(state_path)
    state = _read_state(path)
    prior_digest = _representation_digest(prior)
    bound = state.get("entry_digest") == prior_digest

    retry_until = state.get("retry_after_until")
    if isinstance(retry_until, float) and retry_until > current:
        return stale_entry(prior, "rate-limited")
    expires_at = state.get("expires_at")
    if (bound and prior["fetched_at"] is not None
            and isinstance(expires_at, float)
            and expires_at > current):
        return prior

    # A validator/expiry window belongs to the exact entry it was stored with.
    # An unpersisted newer fetch must not be reused for an older entry.
    etag = state.get("etag") if bound else None
    get = transport or _default_transport
    try:
        request_timeout = min(TIMEOUT_S, float(timeout))
        if request_timeout <= 0:
            return stale_entry(prior, "timeout")
        status, headers, body = get(ENDPOINT, request_timeout, etag)
    except SpecialResetError as exc:
        return stale_entry(prior, exc.reason)
    except TimeoutError:
        return stale_entry(prior, "timeout")
    except Exception:
        logger.debug("codex_special_reset ▸ transport failed", exc_info=True)
        return stale_entry(prior, "network-error")

    header_map = _headers_map(headers)
    try:
        status = int(status)
        if len(body or b"") > MAX_BODY_BYTES:
            raise SpecialResetError("oversize")
        if status == 304:
            if prior["announcement"] is None or not bound:
                return stale_entry(prior, "http-304")
            renewed = dict(prior)
            renewed["fetched_at"] = datetime.fromtimestamp(
                current, timezone.utc).isoformat()
            renewed["stale"] = False
            renewed["unavailable_reason"] = None
            next_state = {
                "expires_at": current + _max_age(header_map),
                "entry_digest": _representation_digest(renewed),
            }
            if header_map.get("etag"):
                next_state["etag"] = header_map["etag"]
            elif etag:
                next_state["etag"] = etag
            _write_state(path, next_state)
            return renewed
        if 300 <= status < 400:
            return stale_entry(prior, "redirect")
        if status == 429:
            wait = _retry_after_seconds(header_map, current) or 60.0
            next_state: dict[str, Any] = {
                "retry_after_until": current + wait,
            }
            if bound:
                if etag:
                    next_state["etag"] = etag
                next_state["expires_at"] = (
                    expires_at if isinstance(expires_at, float) else 0.0)
                next_state["entry_digest"] = prior_digest
            _write_state(path, next_state)
            return stale_entry(prior, "http-429")
        if status != 200:
            return stale_entry(prior, "http-%d" % status)
        try:
            parsed = json.loads((body or b"").decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return stale_entry(prior, "bad-json")
        announcement = select_announcement(parsed)
    except SpecialResetError as exc:
        return stale_entry(prior, exc.reason)
    except Exception:
        logger.debug("codex_special_reset ▸ response parse failed", exc_info=True)
        return stale_entry(prior, "schema")

    entry = {
        "announcement": announcement,
        "fetched_at": datetime.fromtimestamp(current, timezone.utc).isoformat(),
        "stale": False,
        "unavailable_reason": None,
    }
    next_state = {
        "expires_at": current + _max_age(header_map),
        "entry_digest": _representation_digest(entry),
    }
    if header_map.get("etag"):
        next_state["etag"] = header_map["etag"]
    _write_state(path, next_state)
    return entry
