"""Grok (X Premium / SuperGrok) quota fetcher — plugin standalone copy."""

from __future__ import annotations

import json
import os
import tempfile
import urllib.request
import urllib.error
from typing import Any, Optional

from .base import QuotaAccount, QuotaResult, QuotaWindow, build_unavailable, opt_in_flag, urlopen_no_redirect
from .browser_cookies import (
    ChromeCookieError,
    load_chrome_grok_cookies,
    load_firefox_grok_cookies,
)

_GROK_ENDPOINT = "https://grok.com/grok_api_v2.GrokBuildBilling/GetGrokCreditsConfig"
_REST_RATELIMITS_URL = "https://grok.com/rest/rate-limits"
_EMPTY_GRPCWEB_BODY = b"\x00\x00\x00\x00\x00"  # 0x00 frame + 4-byte len(0)
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)

_SESSION_PATH = os.path.join(os.path.expanduser("~"), "grok_session.json")
_RAW_DEBUG_PATH = os.path.join(os.path.expanduser("~"), "grok_last_response.bin")


def _write_private_debug(path: str, raw: bytes) -> None:
    """Atomically replace a private debug file without following target symlinks."""
    parent = os.path.dirname(os.path.abspath(path)) or "."
    prefix = f".{os.path.basename(path)}."
    fd, temp_path = tempfile.mkstemp(prefix=prefix, dir=parent)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fd = -1
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_path, path)
    except Exception:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _load_cookies() -> Optional[str]:
    try:
        with open(_SESSION_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        cookies = data.get("cookies") or data
        if isinstance(cookies, dict):
            cookies = "; ".join(f"{k}={v}" for k, v in cookies.items())
        if cookies and str(cookies).strip():
            return str(cookies)
    except Exception:
        pass

    # Automatic local fallback: Firefox first (plaintext cookies), then
    # Chrome (encrypted). Only grok.com cookies are selected.
    firefox = load_firefox_grok_cookies()
    if firefox:
        return firefox
    return load_chrome_grok_cookies()


def _parse_grok_protobuf(raw: bytes) -> Optional[QuotaResult]:
    import struct

    msg = raw
    if msg[:1] == b"\x00" and len(msg) >= 5:
        length = int.from_bytes(msg[1:5], "big")
        if length and len(msg) >= 5 + length:
            msg = msg[5 : 5 + length]

    def parse(m):
        out = []
        i = 0
        while i < len(m):
            if i >= len(m):
                break
            key = m[i]
            i += 1
            fn = key >> 3
            wire = key & 0x07
            if wire == 0:
                v = 0
                s = 0
                while i < len(m):
                    b = m[i]
                    i += 1
                    v |= (b & 0x7F) << s
                    s += 7
                    if not (b & 0x80):
                        break
                out.append((fn, wire, v))
            elif wire == 2:
                ln = 0
                s = 0
                while i < len(m):
                    b = m[i]
                    i += 1
                    ln |= (b & 0x7F) << s
                    s += 7
                    if not (b & 0x80):
                        break
                d = m[i : i + ln]
                i += ln
                out.append((fn, wire, d))
            elif wire == 5:
                v = struct.unpack("<f", m[i : i + 4])[0]
                i += 4
                out.append((fn, wire, v))
            elif wire == 1:
                v = int.from_bytes(m[i : i + 8], "little")
                i += 8
                out.append((fn, wire, v))
            else:
                break
        return out

    top = parse(msg)
    if not top or top[0][0] != 1 or top[0][1] != 2:
        return None
    inner = parse(top[0][2])

    # Field map (decoded from a live capture, cross-checked against the
    # grok.com usage screen):
    #   fn1 float          — Weekly Limit % used (ABSENT = 0% used; the
    #                        grok.com panel renders "0% utilizado" when the
    #                        field is missing, e.g. free/unused accounts)
    #   fn4 / fn5 msg      — Weekly window start / reset (fn1 = epoch seconds)
    #   fn7 / fn8 msg      — typed breakdown of the SAME window: fn1 = kind,
    #                        fn2 float = % used. The panel draws these as the
    #                        legend under the single bar ("Grok Build 3%"), not
    #                        as extra meters — publishing them as windows showed
    #                        two quotas with the same % and reset date.
    #   fn11 / fn13 varint — flags with no counterpart in the grok.com UI; left
    #                        unreported on purpose. A fn11==1 → "Reset banked:
    #                        available" claim was shipped once and was wrong: the
    #                        Usage panel for the same payload showed only
    #                        "Weekly Limit 3% used / Resets …" and "Grok Build 3%".
    #                        Do not turn an unidentified field into a claim.
    used_percent: Optional[float] = None
    reset_epoch: Optional[int] = None

    def _sub_epoch(blob: bytes) -> Optional[int]:
        for sfn, sw, sv in parse(blob):
            if sfn == 1 and sw == 0 and isinstance(sv, int):
                return sv
        return None

    for fn, wire, v in inner:
        if fn == 1 and wire == 5:
            try:
                used_percent = float(v)
            except (TypeError, ValueError):
                pass
        elif fn == 5 and wire == 2:
            epoch = _sub_epoch(v)
            if epoch is not None and (reset_epoch is None or epoch > reset_epoch):
                reset_epoch = epoch

    if used_percent is None and reset_epoch is None:
        return None

    from datetime import datetime, timezone

    def _iso(epoch: Optional[int]) -> Optional[str]:
        if epoch is None:
            return None
        try:
            return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None

    windows: list[QuotaWindow] = []
    if used_percent is None and reset_epoch is not None:
        # Weekly window present but no usage field: the grok.com panel
        # renders this as "0% utilizado" (free/unused accounts).
        used_percent = 0.0
    if used_percent is not None or reset_epoch is not None:
        windows.append(
            QuotaWindow(
                label="Weekly Limit", used_percent=used_percent, reset_at=_iso(reset_epoch)
            )
        )

    if not windows:
        return None
    return QuotaResult(label="grok", windows=windows, details=[], unavailable_reason=None)


def _fetch_grok_rest(cookies: str) -> Optional[QuotaResult]:
    """Fallback: per-model chat quota JSON endpoint (rest/rate-limits).

    This is a DIFFERENT meter from the billing gRPC above: chat queries per
    model on a rolling 2h window (e.g. ``10/10``), not the Weekly/Grok Build
    usage shown on the grok.com panel. Only reached when the gRPC probe fails.
    Plan name is not exposed anywhere in the Grok API (verified against both
    surfaces), so we never invent one.
    """
    body = json.dumps({"requestKind": "DEFAULT", "modelName": "grok-4"}).encode("utf-8")
    headers = {
        "accept": "*/*",
        "content-type": "application/json",
        "origin": "https://grok.com",
        "referer": "https://grok.com/?_s=usage",
        "user-agent": _BROWSER_UA,
        "cookie": cookies,
    }
    req = urllib.request.Request(_REST_RATELIMITS_URL, data=body, headers=headers, method="POST")
    try:
        with urlopen_no_redirect(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return build_unavailable("grok", "cloudflare-blocked" if e.code == 403 else "auth-failed")
        return None  # fall through to the gRPC probe
    except Exception:
        return None  # fall through to the gRPC probe

    windows: list[QuotaWindow] = []
    remaining = data.get("remainingQueries")
    total = data.get("totalQueries")
    if isinstance(remaining, (int, float)) and isinstance(total, (int, float)) and total > 0:
        used_pct = round(100.0 * (1.0 - float(remaining) / float(total)), 2)
        # No reset instant is reported: windowSizeSeconds is the LENGTH of a
        # rolling window, not an anchor, so any timestamp derived from it would
        # be invented (the panel's real reset comes from the gRPC path).
        windows.append(QuotaWindow(label="2h", used_percent=used_pct, reset_at=None))

    def _eff(block, label):
        if not isinstance(block, dict):
            return
        rem, tot = block.get("remainingQueries"), block.get("totalQueries")
        if isinstance(rem, (int, float)) and isinstance(tot, (int, float)) and tot > 0:
            windows.append(
                QuotaWindow(
                    label=label,
                    used_percent=round(100.0 * (1.0 - float(rem) / float(tot)), 2),
                )
            )

    _eff(data.get("lowEffortRateLimits"), "low effort")
    _eff(data.get("highEffortRateLimits"), "high effort")

    if not windows:
        return None
    return QuotaResult(label="grok", windows=windows, plan=None, unavailable_reason=None)


def fetch_grok_quota() -> QuotaResult:
    try:
        cookies = _load_cookies()
    except ChromeCookieError as exc:
        return build_unavailable("grok", exc.reason)
    if not cookies:
        return build_unavailable("grok", "no-session-cookies")

    # Primary: the billing gRPC — this is the "Weekly Limit" + "Grok Build"
    # usage screen (percent used + reset). Verified 1:1 against the grok.com
    # usage panel: same percentages, same reset instant. Fields the panel does
    # not render (fn11/fn13 flags) are deliberately not reported.
    headers = {
        "accept": "*/*",
        "content-type": "application/grpc-web+proto",
        "origin": "https://grok.com",
        "referer": "https://grok.com/?_s=usage",
        "user-agent": _BROWSER_UA,
        "x-grpc-web": "1",
        "x-user-agent": "connect-es/2.1.1",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "cookie": cookies,
    }
    req = urllib.request.Request(_GROK_ENDPOINT, data=_EMPTY_GRPCWEB_BODY, headers=headers, method="POST")
    try:
        with urlopen_no_redirect(req, timeout=15) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return build_unavailable("grok", "cloudflare-blocked" if e.code == 403 else "auth-failed")
        return build_unavailable("grok", f"http-{e.code}")
    except Exception as e:
        return build_unavailable("grok", f"fetch-error:{type(e).__name__}")
    if os.environ.get("HERMES_QUOTA_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}:
        try:
            _write_private_debug(_RAW_DEBUG_PATH, raw)
        except Exception:
            pass
    result = _parse_grok_protobuf(raw)
    if result is not None:
        return result

    # Fallback: the per-model chat quota (rest/rate-limits). NOTE: this is a
    # DIFFERENT meter — chat queries per model on a rolling 2h window, not the
    # Build/weekly usage the panel shows. Only used when the gRPC parse fails.
    rest = _fetch_grok_rest(cookies)
    if rest is not None:
        return rest
    return build_unavailable("grok", "parse-pending")


from .registry import register as _register  # noqa: E402

# Grok is opt-in: reads browser cookies (Firefox grok.com, then Chrome on
# macOS). Disabled by default; enable with:
#   hermes config set plugins.entries.quota.settings.grokEnabled true
import os as _os

# Resolve opt-in from plugin config (set via `hermes config set ...`), falling
# back to the HERMES_QUOTA_GROK_ENABLED env var. Config wins so the UI toggle
# and CLI config are the single source of truth.
def _grok_enabled() -> bool:
    val = _os.environ.get("HERMES_QUOTA_GROK_ENABLED", "").strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    try:
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly() or {}
        plugins = cfg.get("plugins") if isinstance(cfg, dict) else None
        entries = plugins.get("entries") if isinstance(plugins, dict) else None
        entry = entries.get("quota") if isinstance(entries, dict) else None
        if isinstance(entry, dict):
            settings = entry.get("settings")
            if isinstance(settings, dict) and "grokEnabled" in settings:
                return opt_in_flag(settings.get("grokEnabled"))
    except Exception:
        pass
    return False


def _fetch_grok_optin() -> Optional["QuotaResult"]:
    if not _grok_enabled():
        return build_unavailable("grok", "opt-in-disabled")
    return fetch_grok_quota()


# Unofficial Grok CLI billing surface. It is not api.x.ai and not the public
# Management API. Shape verified against a live Hermes xai-oauth login.
_OAUTH_BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
_OAUTH_SETTINGS_URL = "https://cli-chat-proxy.grok.com/v1/settings"
_OAUTH_TIMEOUT_S = 4.0
_OAUTH_BUDGET_S = 12.0
_MAX_OAUTH_ACCOUNTS = 4
_PRODUCT_NAMES = {"GrokBuild": "Grok Build", "GrokChat": "Grok Chat"}


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _money_val(config: dict, key: str) -> Optional[float]:
    raw = config.get(key)
    if not isinstance(raw, dict):
        return None
    return _finite(raw.get("val"))


def _product_name(value: Any) -> str:
    name = str(value or "").strip()
    if name in _PRODUCT_NAMES:
        return _PRODUCT_NAMES[name]
    spaced = []
    for char in name.replace("_", " ").replace("-", " "):
        if spaced and char.isupper() and spaced[-1].islower():
            spaced.append(" ")
        spaced.append(char)
    return " ".join("".join(spaced).split()).title() or "Unknown product"


def _known_expired(token: str) -> bool:
    """Use JWT expiry only to discard stale snapshots, never to authenticate.

    Opaque, malformed or missing-exp tokens remain vendor-validated.
    """
    import base64
    import math
    import time

    try:
        parts = token.split(".")
        if len(parts) != 3 or len(parts[1]) > 16384:
            return False
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        expiry = payload.get("exp") if isinstance(payload, dict) else None
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float)):
            return False
        return math.isfinite(expiry) and expiry <= time.time()
    except (ValueError, UnicodeError, OverflowError):
        return False


def _stale_duplicate(token: str, alternatives: list[str]) -> bool:
    """Expiry alone cannot prove two credentials represent the same account."""
    from .history_identity import identity_hint
    hint = identity_hint("grok", token)
    return bool(hint and _known_expired(token) and any(
        not _known_expired(other) and identity_hint("grok", other) == hint
        for other in alternatives
    ))


def _saved_xai_tokens() -> list[str]:
    """Snapshot saved xai-oauth access tokens. Never refresh, select, or write."""
    try:
        from hermes_constants import get_default_hermes_root, get_hermes_home
    except Exception:
        return []
    homes = []
    for home in (get_hermes_home(), get_default_hermes_root()):
        if home not in homes:
            homes.append(home)
    found: list[str] = []
    legacy: list[str] = []
    pooled: list[str] = []
    for home in homes:
        try:
            store = json.loads((home / "auth.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, UnicodeError):
            continue
        if not isinstance(store, dict):
            continue
        providers = store.get("providers") if isinstance(store.get("providers"), dict) else {}
        state = providers.get("xai-oauth") if isinstance(providers, dict) else None
        tokens = state.get("tokens") if isinstance(state, dict) else None
        access = tokens.get("access_token") if isinstance(tokens, dict) else None
        if isinstance(access, str) and access.strip():
            found.append(access.strip())
            legacy.append(access.strip())
        pool = store.get("credential_pool") if isinstance(store.get("credential_pool"), dict) else {}
        rows = pool.get("xai-oauth") if isinstance(pool, dict) else None
        for row in rows if isinstance(rows, list) else ():
            if not isinstance(row, dict):
                continue
            access = row.get("access_token")
            if isinstance(access, str) and access.strip():
                found.append(access.strip())
                pooled.append(access.strip())
    # The legacy provider field may retain an old snapshot after pool rotation.
    # It is not evidence of another account. Preserve explicitly saved pool
    # entries (even failing ones), and keep legacy-only failures visible.
    stale_legacy = {
        token for token in legacy if token not in pooled and _stale_duplicate(token, pooled)
    }
    unique: list[str] = []
    for token in found:
        if token not in unique and token not in stale_legacy:
            unique.append(token)
    return unique[:_MAX_OAUTH_ACCOUNTS]


def _oauth_tokens() -> list[str]:
    """Hermes-owned xai-oauth tokens, read-only. Unexpected resolver errors do not peek another account."""
    tokens: list[str] = []
    saved = _saved_xai_tokens()
    try:
        from hermes_cli.auth import AuthError
        from hermes_cli.auth_xai import resolve_xai_oauth_runtime_credentials
    except Exception:
        return saved
    try:
        creds = resolve_xai_oauth_runtime_credentials(refresh_if_expiring=False)
        token = str((creds or {}).get("api_key") or "").strip()
        # Some core versions resolve the old provider snapshot before the pool.
        # Do not put that expired snapshot back after the saved-token scan
        # excluded it. Explicit saved entries and resolver-only failure remain.
        if token and (token in saved or not _stale_duplicate(token, saved)):
            tokens.append(token)
    except AuthError:
        pass
    except Exception:
        return []
    for token in saved:
        if token not in tokens:
            tokens.append(token)
        if len(tokens) >= _MAX_OAUTH_ACCOUNTS:
            break
    return tokens[:_MAX_OAUTH_ACCOUNTS]


def _oauth_get(url: str, token: str) -> tuple[Optional[dict], Optional[str]]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "x-xai-token-auth": "xai-grok-cli",
        "x-grok-client-mode": "cli",
        "User-Agent": "hermes-quota-plugin",
    }
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urlopen_no_redirect(request, timeout=_OAUTH_TIMEOUT_S) as response:
            body = response.read(65537)
    except urllib.error.HTTPError as exc:
        if exc.fp is not None:
            exc.close()
        if exc.code in (401, 403):
            return None, "auth-failed"
        return None, f"http-{exc.code}"
    except Exception as exc:
        return None, f"fetch-error:{type(exc).__name__}"
    if len(body) > 65536:
        return None, "response-too-large"
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError):
        return None, "bad-json"
    if not isinstance(payload, dict):
        return None, "bad-json"
    return payload, None


def _oauth_account(token: str, index: int) -> QuotaResult:
    billing, reason = _oauth_get(_OAUTH_BILLING_URL, token)
    if billing is None:
        return build_unavailable("grok", reason or "no-data")
    config = billing.get("config")
    config = config if isinstance(config, dict) else {}
    period = config.get("currentPeriod")
    period = period if isinstance(period, dict) else {}
    period_type = str(period.get("type") or "")
    label = "Weekly pool" if "WEEKLY" in period_type.upper() else "Usage pool"
    used = _finite(config.get("creditUsagePercent"))
    reset = period.get("end") if isinstance(period.get("end"), str) and period.get("end") else None
    windows: list[QuotaWindow] = []
    if used is not None:
        windows.append(QuotaWindow(label=label, used_percent=max(0.0, min(100.0, used)), reset_at=reset))
    details: list[str] = []
    for product in config.get("productUsage") if isinstance(config.get("productUsage"), list) else []:
        if not isinstance(product, dict):
            continue
        percent = _finite(product.get("usagePercent"))
        if percent is None:
            continue
        details.append(f"{_product_name(product.get('product'))}: {percent:g}% used")
    cap, spent = _money_val(config, "onDemandCap"), _money_val(config, "onDemandUsed")
    if cap == 0:
        details.append("Pay-as-you-go: off")
    elif cap is not None:
        spent_part = f", used {spent:g} raw units" if spent is not None else ""
        details.append(f"Pay-as-you-go: on (cap {cap:g} raw units{spent_part})")
    prepaid = _money_val(config, "prepaidBalance")
    if prepaid == 0:
        details.append("Prepaid balance: $0")
    elif prepaid is not None:
        details.append(f"Prepaid balance: {prepaid:g} raw units")
    plan = None
    settings, _settings_reason = _oauth_get(_OAUTH_SETTINGS_URL, token)
    if isinstance(settings, dict):
        tier = settings.get("subscription_tier_display")
        if isinstance(tier, str) and tier.strip():
            plan = tier.strip()
    if not windows:
        return build_unavailable("grok", "subscription-usage-unavailable", details=details)
    result = QuotaResult(label="grok", windows=windows, plan=plan, details=details)
    result.account_label = f"Account {index}"
    return result


def _fetch_grok_oauth() -> QuotaResult:
    """Subscription quota from Hermes xai-oauth. Never uses on-demand spend as the percentage."""
    import time

    tokens = _oauth_tokens()
    if not tokens:
        return build_unavailable("grok", "no-credentials")
    deadline = time.monotonic() + _OAUTH_BUDGET_S
    results: list[QuotaResult] = []
    for index, token in enumerate(tokens, start=1):
        if time.monotonic() >= deadline:
            results.append(build_unavailable("grok", "timeout"))
            break
        item = _oauth_account(token, index)
        from .history_identity import identity_hint
        item.history_id = identity_hint("grok", token)
        results.append(item)
    chosen = next((item for item in results if item.has_data()), results[0])
    extras = [item for item in results if item is not chosen]
    if extras:
        accounts = []
        for offset, item in enumerate(extras, start=2):
            accounts.append(QuotaAccount(
                id=f"oauth-{offset}",
                label=item.account_label or f"Account {offset}",
                windows=list(item.windows),
                plan=item.plan,
                unavailable_reason=item.unavailable_reason,
                details=list(item.details),
                history_id=item.history_id,
            ))
        chosen.accounts = accounts
    return chosen


def _fetch_grok_registered() -> QuotaResult:
    """OAuth subscription quota first. Browser cookies remain the opt-in last resort."""
    oauth = _fetch_grok_oauth()
    if oauth.has_data():
        return oauth
    if oauth.unavailable_reason == "auth-failed" and _grok_enabled():
        cookies = fetch_grok_quota()
        if cookies is not None and cookies.has_data():
            return cookies
    if oauth.unavailable_reason != "no-credentials":
        return oauth
    if _grok_enabled():
        return fetch_grok_quota()
    return build_unavailable(
        "grok",
        "no-credentials",
        details=["No Hermes xai-oauth token. Browser cookie fallback is opt-in and disabled."],
    )


_register("grok")(_fetch_grok_registered)
