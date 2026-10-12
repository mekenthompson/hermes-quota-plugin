"""Pseudonymous history identity hints, not authentication or token hashes."""
import base64
import hashlib
import json


def identity_hint(provider, token, effective_account=None):
    try:
        part = token.split(".")[1]
        if len(part) > 16384:
            return None
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        if not isinstance(claims, dict):
            return None
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            return None
        account = claims.get("https://api.openai.com/auth", {})
        account = account.get("chatgpt_account_id", "") if isinstance(account, dict) else ""
        if isinstance(effective_account, str) and effective_account.strip():
            account = effective_account.strip()
        if provider == "openai-codex" and (not isinstance(account, str) or not account):
            return None
        identity = json.dumps([provider, claims.get("iss", ""), subject, account], separators=(",", ":"))
        return hashlib.sha256(identity.encode()).hexdigest()
    except (ValueError, IndexError, UnicodeError, TypeError):
        return None
