"""Read-only quota page for the Hermes dashboard. Mounted at /api/plugins/quota/."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter

router = APIRouter()

_SECRET = re.compile(r"(bearer\s+|sk-|eyJ[A-Za-z0-9_-]{20,})", re.IGNORECASE)


def _text(value: Any, limit: int = 240) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or _SECRET.search(text) or len(text) > limit:
        return None
    return text


def _window(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    used = raw.get("used_percent")
    if isinstance(used, bool) or not isinstance(used, (int, float)):
        used = None
    else:
        used = max(0.0, min(100.0, float(used)))
    reset = _text(raw.get("reset_at"), 80)
    label = _text(raw.get("label"), 80) or "window"
    if used is None and reset is None:
        return None
    return {"label": label, "used_percent": used, "reset_at": reset}


def _record(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"label": "unknown", "windows": [], "details": [], "unavailable_reason": "bad-record"}
    details = []
    details_raw = raw.get("details")
    for item in details_raw if isinstance(details_raw, list) else []:
        text = _text(item)
        if text:
            details.append(text)
    windows_raw = raw.get("windows")
    windows = [item for item in (_window(w) for w in (windows_raw if isinstance(windows_raw, list) else [])) if item]
    accounts = []
    accounts_raw = raw.get("accounts")
    for account in accounts_raw if isinstance(accounts_raw, list) else []:
        if isinstance(account, dict):
            accounts.append(_record(account))
    return {
        "label": _text(raw.get("label"), 80) or "account",
        "plan": _text(raw.get("plan"), 80),
        "account_label": _text(raw.get("account_label"), 80),
        "unavailable_reason": _text(raw.get("unavailable_reason"), 80),
        "details": details[:8],
        "windows": windows[:8],
        "accounts": accounts[:8],
    }


def _cache():
    import importlib
    import importlib.util
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    name = "hermes_quota_plugin"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, root / "__init__.py", submodule_search_locations=[str(root)]
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("quota plugin package could not be loaded")
        pkg = importlib.util.module_from_spec(spec)
        sys.modules[name] = pkg
        spec.loader.exec_module(pkg)
    return importlib.import_module(name + ".quota_cache")

def public_snapshot() -> dict[str, Any]:
    """Quota cache with only display fields. Never returns credentials."""
    module = _cache()
    cache = module.read_quota_cache() or {}
    providers = cache.get("providers") if isinstance(cache, dict) else {}
    if not isinstance(providers, dict):
        providers = {}
    return {
        "fetched_at": _text(cache.get("fetched_at"), 80) if isinstance(cache, dict) else None,
        "managed_collection": bool(getattr(module, "managed_collection_enabled", lambda: False)()),
        "providers": {name: _record(record) for name, record in providers.items() if _text(name, 40)},
    }


@router.get("/history")
def quota_history(days: int = 30) -> dict[str, Any]:
    import importlib
    from fastapi import HTTPException
    history = importlib.import_module(_cache().__package__ + ".quota_history")
    if not 1 <= days <= 90:
        raise HTTPException(status_code=400, detail="history days must be between 1 and 90")
    rows = history.read_history(days=days)
    return {"samples": rows, "daily_changes": history.daily_changes(rows)}


@router.get("/forecast")
def quota_forecast() -> dict[str, Any]:
    import importlib
    history = importlib.import_module(_cache().__package__ + ".quota_history")
    return {"forecasts": history.forecasts(history.read_history(days=2))}


@router.get("/status")
def quota_status() -> dict[str, Any]:
    return public_snapshot()


@router.post("/refresh")
def quota_refresh() -> dict[str, Any]:
    module = _cache()
    if not getattr(module, "managed_collection_enabled", lambda: False)():
        module.refresh_quota_cache()
    return public_snapshot()
