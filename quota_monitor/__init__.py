"""Agent Bar / Agent Monitor features inside the quota plugin.

Entry points (each returns one JSON-able dict carrying ``monitor_version`` and ``generated_at``):

* ``snapshot()``  — ``hermes quota monitor snapshot --json`` (one call per widget poll);
* ``history_payload(days)`` — ``hermes quota monitor history --json``;
* ``recommend_payload(...)`` — ``hermes quota monitor recommend --json``;
* ``set_budget(daily, monthly)`` / ``budget_payload()`` — the budget the widget keeps in sync;
* ``accounts_view()`` — ``hermes quota monitor accounts --json`` (credential pools, strategies, fallbacks,
  sign-in gaps); ``set_priority(...)`` / ``set_strategy(...)`` refuse in the trial.

Every reader and store entry point checks enrollment first; while the monitor is OFF no
file, sqlite database or process table is read or written. Account/budget writes, agent
tools and shared-Kanban reads are unavailable. Costs are API-price estimates, never
billed amounts, and there is no month-end projection (``projection_available: false``).
Network is never touched here (quota numbers come from the plugin's ``quota_cache.json``).
See docs/monitor.md for the full contract.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional

from . import trial
from .common import MONITOR_VERSION, day_range, hermes_root, profile_homes
from .trial import TrialError

SNAPSHOT_TOP_DAYS = 7
SNAPSHOT_CLI_SESSIONS = 20
HISTORY_MAX_DAYS = 31


def _stamp(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = {"monitor_version": MONITOR_VERSION, "generated_at": time.time()}
    out.update(payload)
    return out


def _error_text(e: BaseException) -> str:
    return ("%s: %s" % (type(e).__name__, e))[:300]


def _disabled(e: trial.TrialError) -> Dict[str, Any]:
    """One honest answer when the monitor is not enrolled: no section is collected."""
    return _stamp({"enrolled": False, "collection": "disabled", "error": str(e)[:300], "error_code": e.code})


def _run(errors: Dict[str, str], timings: Dict[str, int], name: str, fn: Callable[[], Any], default: Any = None) -> Any:
    t0 = time.time()
    try:
        return fn()
    except Exception as e:  # one failing section must never fail the call
        errors[name] = _error_text(e)
        return default
    finally:
        timings[name] = int((time.time() - t0) * 1000)


def record_quota_history() -> int:
    """Append the current quota cache to the history (no-op when that cache was already recorded).

    Enrollment gates this: an un-enrolled monitor writes no history at all.
    """
    from . import quotas
    return quotas.record_from_cache()


def _health(homes: List[Any], items: List[Dict[str, Any]]) -> Dict[str, Any]:
    from . import health, live
    h = health.health(homes, items)
    h["speed"] = live.model_speed_cached(homes)
    return h


def snapshot(record_sample: bool = True) -> Dict[str, Any]:
    from . import clis, live, quotas, usage

    try:
        trial.require_enrollment()
    except trial.TrialError as e:
        return _disabled(e)
    errors: Dict[str, str] = {}
    timings: Dict[str, int] = {}
    homes = profile_homes()
    root = hermes_root()
    now = time.time()
    _run(errors, timings, "history_record", quotas.record_from_cache)
    items = _run(errors, timings, "quota_items", quotas.items_from_cache, []) or []

    def _live() -> Dict[str, Any]:
        data = live.live(homes, now, record_sample=record_sample)
        data["burn"] = quotas.burn(now)
        return data

    def _clis() -> Dict[str, Any]:
        start, end, days = day_range(date.today() - timedelta(days=SNAPSHOT_TOP_DAYS - 1), date.today())
        data = clis.summary(start, end, days)
        data["sessions"] = data["sessions"][:SNAPSHOT_CLI_SESSIONS]
        return data

    def _top() -> List[Dict[str, Any]]:
        start, end, _ = day_range(date.today() - timedelta(days=SNAPSHOT_TOP_DAYS - 1), date.today())
        return usage.top_sessions(start, end, homes)

    out: Dict[str, Any] = {
        "live": _run(errors, timings, "live", _live),
        "health": _run(errors, timings, "health", lambda: _health(homes, items)),
        "kanban": _run(errors, timings, "kanban", lambda: usage.kanban_glance(root, now)),
        "usage": _run(errors, timings, "usage", lambda: usage.usage_today_month(homes)),
        "top_sessions": _run(errors, timings, "top_sessions", _top),
        "clis": _run(errors, timings, "clis", _clis),
    }
    out["errors"] = errors
    out["timings_ms"] = timings
    return _stamp(out)


def history_payload(days: int = 1) -> Dict[str, Any]:
    from . import quotas
    try:
        trial.require_enrollment()
    except trial.TrialError as e:
        return _disabled(e)
    days = max(1, min(HISTORY_MAX_DAYS, int(days or 1)))
    errors: Dict[str, str] = {}
    timings: Dict[str, int] = {}
    now = time.time()
    _run(errors, timings, "history_record", quotas.record_from_cache)
    out = {"days": days,
           "history": _run(errors, timings, "history", lambda: quotas.history(now - days * 86400, now + 60), []),
           "burn": _run(errors, timings, "burn", lambda: quotas.burn(now), []),
           "errors": errors}
    return _stamp(out)


def _routing_inputs() -> Dict[str, Any]:
    from . import quotas
    homes = profile_homes()
    try:
        quotas.record_from_cache()
    except Exception:
        pass
    items = quotas.items_from_cache()
    return {"items": items, "burn": quotas.burn(), "health": _health(homes, items)}


def recommend_payload(prefer_provider: Optional[str] = None, exclude_providers: Optional[List[str]] = None,
                      min_quota_left_pct: int = 10, top: int = 3) -> Dict[str, Any]:
    from . import advice
    try:
        trial.require_enrollment()
    except trial.TrialError as e:
        return _disabled(e)
    inputs = _routing_inputs()
    out = advice.recommend_model(inputs["items"], inputs["burn"], inputs["health"], prefer_provider,
                                 exclude_providers, min_quota_left_pct, top)
    return _stamp(out)


def read_budget() -> Dict[str, Any]:
    from . import store
    budget = store.read_prefs().get("budget")
    budget = budget if isinstance(budget, dict) else {}

    def val(k: str) -> Optional[float]:
        v = budget.get(k)
        return float(v) if isinstance(v, (int, float)) and v > 0 else None

    return {"daily": val("daily"), "monthly": val("monthly")}


def set_budget(daily: Optional[float] = None, monthly: Optional[float] = None) -> Dict[str, Any]:
    """Unavailable in the read-only trial: no preference write path is enabled yet.

    Returns the structured refusal (rather than raising) so the one-JSON-object CLI contract keeps the
    machine-readable code.
    """
    return _stamp({"error": "Budget changes are unavailable in the read-only trial",
                   "error_code": "trial-read-only"})


def budget_payload() -> Dict[str, Any]:
    from . import advice, clis, usage
    try:
        trial.require_enrollment()
    except trial.TrialError as e:
        return _disabled(e)
    today = date.today()
    start, end, days = day_range(today.replace(day=1), today)
    month = usage.usage(start, end, days, profile_homes())
    try:
        cli_daily = clis.summary(start, end, days)["daily"]
    except Exception:
        cli_daily = []
    return _stamp(advice.spend_summary(month, cli_daily, read_budget(), today))


def accounts_view() -> Dict[str, Any]:
    from . import accounts
    try:
        trial.require_enrollment()
    except trial.TrialError as e:
        return _disabled(e)
    out = accounts.accounts_payload()
    return _stamp(out)


def _write_result(fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    from . import accounts
    try:
        res = fn()
    except accounts.AccountsError as e:
        out = {"error": str(e)[:400], "error_code": e.code}
        out.update({k: v for k, v in e.extra.items() if k in ("owner", "used_by", "command")})
        return _stamp(out)
    state = res.pop("state", None)
    if isinstance(state, dict):
        res["state"] = _stamp(state)
    return _stamp(res)


def set_priority(provider: str, target: str, priority: Any, profile: Optional[str] = None) -> Dict[str, Any]:
    from . import accounts
    return _write_result(lambda: accounts.set_priority(provider, target, priority, profile))


def set_strategy(provider: str, strategy: str, profile: Optional[str] = None) -> Dict[str, Any]:
    from . import accounts
    return _write_result(lambda: accounts.set_strategy(provider, strategy, profile))
