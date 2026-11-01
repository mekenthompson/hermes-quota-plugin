"""Retained shared-subscription Grok CLI billing contract, not grok.com pools.

Adapted from house 51baa67. Tokens stay with their owner; only the native
resolver may renew them. This module never discovers browser cookies.
"""
from __future__ import annotations
import base64
import json
import math
import time
import urllib.error
import urllib.request
from .base import QuotaAccount, QuotaResult, QuotaWindow, build_unavailable, urlopen_no_redirect
from .history_identity import identity_hint

_OAUTH_BILLING_URL = 'https://cli-chat-proxy.grok.com/v1/billing?format=credits'
_OAUTH_SETTINGS_URL = 'https://cli-chat-proxy.grok.com/v1/settings'
_OAUTH_TIMEOUT_S = 4.0
_OAUTH_BUDGET_S = 12.0
_MAX_OAUTH_ACCOUNTS = 4


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def _known_expired(token):
    try:
        parts = token.split('.')
        if len(parts) != 3 or len(parts[1]) > 16384:
            return False
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + '=' * (-len(parts[1]) % 4)))
        expiry = payload.get('exp') if isinstance(payload, dict) else None
        return (isinstance(expiry, (int, float)) and not isinstance(expiry, bool)
                and math.isfinite(expiry) and expiry <= time.time())
    except (ValueError, UnicodeError, OverflowError):
        return False


def _stale_duplicate(token, alternatives):
    hint = identity_hint('grok', token)
    return bool(hint and _known_expired(token) and any(
        not _known_expired(other) and identity_hint('grok', other) == hint for other in alternatives))


def _saved_xai_tokens():
    try:
        from hermes_constants import get_default_hermes_root, get_hermes_home
    except ImportError:
        return []
    found, legacy, pooled = [], [], []
    for home in dict.fromkeys((get_hermes_home(), get_default_hermes_root())):
        try:
            store = json.loads((home / 'auth.json').read_text(encoding='utf-8-sig'))
        except (OSError, ValueError, UnicodeError):
            continue
        if not isinstance(store, dict):
            continue
        providers = store.get('providers')
        state = providers.get('xai-oauth') if isinstance(providers, dict) else None
        tokens = state.get('tokens') if isinstance(state, dict) else None
        access = tokens.get('access_token') if isinstance(tokens, dict) else None
        if isinstance(access, str) and access.strip():
            found.append(access.strip()); legacy.append(access.strip())
        pool = store.get('credential_pool')
        rows = pool.get('xai-oauth') if isinstance(pool, dict) else None
        for row in rows if isinstance(rows, list) else []:
            access = row.get('access_token') if isinstance(row, dict) else None
            if isinstance(access, str) and access.strip():
                found.append(access.strip()); pooled.append(access.strip())
    stale = {t for t in legacy if t not in pooled and _stale_duplicate(t, pooled)}
    return list(dict.fromkeys(t for t in found if t not in stale))[:_MAX_OAUTH_ACCOUNTS]


def _without_stale_duplicates(tokens):
    return list(dict.fromkeys(t for t in tokens if not _stale_duplicate(t, [o for o in tokens if o != t])))[:_MAX_OAUTH_ACCOUNTS]


def _oauth_tokens():
    try:
        from hermes_cli.auth import AuthError
        from hermes_cli.auth_xai import resolve_xai_oauth_runtime_credentials
    except ImportError:
        return _saved_xai_tokens()
    try:
        creds = resolve_xai_oauth_runtime_credentials(refresh_if_expiring=True)
    except AuthError:
        return _without_stale_duplicates(_saved_xai_tokens())
    except Exception:
        return []
    saved = _saved_xai_tokens()
    token = str((creds or {}).get('api_key') or '').strip()
    tokens = [token] if token and (token in saved or not _stale_duplicate(token, saved)) else []
    return _without_stale_duplicates(tokens + saved)


def _oauth_get(url, token):
    request = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}',
        'Accept': 'application/json', 'x-xai-token-auth': 'xai-grok-cli',
        'x-grok-client-mode': 'cli', 'User-Agent': 'hermes-quota-plugin'}, method='GET')
    try:
        with urlopen_no_redirect(request, timeout=_OAUTH_TIMEOUT_S) as response:
            body = response.read(65537)
    except urllib.error.HTTPError as exc:
        if exc.fp is not None: exc.close()
        return None, 'auth-failed' if exc.code in (401, 403) else f'http-{exc.code}'
    except Exception as exc:
        return None, f'fetch-error:{type(exc).__name__}'
    if len(body) > 65536: return None, 'response-too-large'
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError):
        return None, 'bad-json'
    return (payload, None) if isinstance(payload, dict) else (None, 'bad-json')


def _oauth_account(token, index):
    billing, reason = _oauth_get(_OAUTH_BILLING_URL, token)
    if billing is None: return build_unavailable('grok', reason or 'no-data')
    config = billing.get('config')
    config = config if isinstance(config, dict) else {}
    period = config.get('currentPeriod')
    period = period if isinstance(period, dict) else {}
    weekly = 'WEEKLY' in str(period.get('type') or '').upper()
    used = _finite(config.get('creditUsagePercent'))
    details = []
    for product in config.get('productUsage') if isinstance(config.get('productUsage'), list) else []:
        if not isinstance(product, dict): continue
        percent = _finite(product.get('usagePercent'))
        name = {'GrokBuild': 'Grok Build', 'GrokChat': 'Grok Chat'}.get(product.get('product'), 'Other product')
        if percent is not None: details.append(f'{name}: {percent:g}% used')
    cap = config.get('onDemandCap')
    cap = _finite(cap.get('val')) if isinstance(cap, dict) else None
    if cap == 0: details.append('Pay-as-you-go: off')
    elif cap is not None:
        spent = config.get('onDemandUsed')
        spent = _finite(spent.get('val')) if isinstance(spent, dict) else None
        suffix = f', used {spent:g} raw units' if spent is not None else ''
        details.append(f'Pay-as-you-go: on (cap {cap:g} raw units{suffix})')
    prepaid = config.get('prepaidBalance')
    prepaid = _finite(prepaid.get('val')) if isinstance(prepaid, dict) else None
    if prepaid == 0: details.append('Prepaid balance: $0')
    elif prepaid is not None: details.append(f'Prepaid balance: {prepaid:g} raw units')
    if used is None: return build_unavailable('grok', 'subscription-usage-unavailable', details=details)
    settings, _ = _oauth_get(_OAUTH_SETTINGS_URL, token)
    plan = settings.get('subscription_tier_display') if isinstance(settings, dict) else None
    reset = period.get('end') if isinstance(period.get('end'), str) else None
    window = QuotaWindow(label='Weekly pool' if weekly else 'Usage pool',
                         used_percent=max(0.0, min(100.0, used)), reset_at=reset,
                         duration_seconds=604800 if period.get('type') == 'USAGE_PERIOD_TYPE_WEEKLY' else None)
    result = QuotaResult(label='grok', windows=[window], plan=plan, details=details)
    result.account_label = f'Account {index}'
    return result


def _fetch_grok_oauth():
    tokens = _oauth_tokens()
    if not tokens: return build_unavailable('grok', 'no-credentials')
    deadline = time.monotonic() + _OAUTH_BUDGET_S
    results = []
    for index, token in enumerate(tokens, start=1):
        item = (build_unavailable('grok', 'timeout') if time.monotonic() >= deadline
                else _oauth_account(token, index))
        item.history_id = identity_hint('grok', token)
        results.append(item)
    chosen = next((r for r in results if r.has_data()), results[0])
    chosen.accounts = [QuotaAccount(id='oauth-' + (r.history_id or str(i)),
        label=r.account_label or f'Account {i}', windows=list(r.windows), plan=r.plan,
        unavailable_reason=r.unavailable_reason, details=list(r.details), history_id=r.history_id)
        for i,r in enumerate(results, start=1) if r is not chosen]
    return chosen


def _grok_enabled():
    from .grok import _grok_enabled as enabled
    return enabled()


def fetch_grok_quota():
    from .grok import fetch_grok_quota as fetch
    return fetch()


def _fetch_grok_registered():
    oauth = _fetch_grok_oauth()
    if oauth.has_data():
        return oauth
    if oauth.unavailable_reason in ('auth-failed', 'no-credentials') and _grok_enabled():
        return fetch_grok_quota()
    if oauth.unavailable_reason == 'no-credentials':
        oauth.details = ['No Hermes xai-oauth token. Browser cookie fallback is opt-in and disabled.']
    return oauth
