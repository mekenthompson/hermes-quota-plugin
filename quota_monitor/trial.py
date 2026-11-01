"""Backend policy for the local, read-only Agent Monitor trial.

None of these boundaries are display preferences: they are enforced in the collection code so that a
widget toggle, a direct CLI call or a direct Python import all meet the same limits.

* monitor data is OFF until ``plugins.entries.quota.settings.monitorEnabled`` is the literal YAML
  boolean ``true`` in the active profile's ``config.yaml`` (strings or 1/0 are not consent);
* only the active profile is in scope, whatever homes a caller passes;
* external agent-CLI stores (Claude Code, Copilot, ...) are never read;
* Kanban lives at the shared Hermes root, so the trial does not read any board;
* agent tools do not register and reject direct calls;
* account and budget mutations are unavailable.

A supported, sanitized host API for credential-pool *metadata* does not exist in this trial, so that
metadata is reported as unavailable instead of being read from ``auth.json`` or reported as "no
credentials".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .common import hermes_home, read_config

ENROLLMENT_SETTING = "plugins.entries.quota.settings.monitorEnabled"
METADATA_REASON = "no-supported-sanitized-host-api"
EXTERNAL_CLI_REASON = "external-cli-collection-disabled"
KANBAN_REASON = "shared-kanban-disabled"

EXTERNAL_CLI_COLLECTION = False
ACCOUNT_METADATA = False
SHARED_KANBAN = False
AGENT_TOOLS = False
ACCOUNT_MUTATIONS = False
BUDGET_WRITES = False


class TrialError(RuntimeError):
    """An operation the read-only trial refuses; ``code`` is machine readable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _value(cfg: Dict[str, Any], dotted: str) -> Any:
    node: Any = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def monitor_enrolled(home: Optional[Path] = None) -> bool:
    """Strictly ``true`` in the active profile's config; anything else (including "true"/1) is OFF."""
    cfg = read_config(Path(home) if home is not None else hermes_home())
    return _value(cfg, ENROLLMENT_SETTING) is True


def enrollment_status(home: Optional[Path] = None) -> Dict[str, Any]:
    """The setting's effective state, without echoing unrelated config values."""
    cfg = read_config(Path(home) if home is not None else hermes_home())
    raw = _value(cfg, ENROLLMENT_SETTING)
    return {"enrolled": raw is True, "setting": ENROLLMENT_SETTING,
            "reason": None if raw is True else "monitor-enrollment-off",
            "invalid_value": raw is not None and not isinstance(raw, bool)}


def require_enrollment() -> None:
    if not monitor_enrolled():
        raise TrialError("monitor-enrollment-off",
                         "Agent Monitor collection is off: set %s to true to enroll" % ENROLLMENT_SETTING)


def active_label() -> str:
    """The active profile's real label, independent of any caller-supplied name."""
    active = hermes_home()
    return active.name if active.parent.name == "profiles" else "default"


def scoped_root(root: Optional[Path] = None) -> Path:
    """Return the active root only; a caller-supplied root cannot escape the active profile."""
    active = hermes_home()
    active_root = active.parent.parent if active.parent.name == "profiles" else active
    if root is None:
        return active_root
    try:
        given = Path(root).resolve()
        active_res = active.resolve()
        root_res = active_root.resolve()
    except OSError:
        raise TrialError("monitor-scope-violation", "profile scope could not be resolved")
    if given not in (root_res, active_res):
        raise TrialError("monitor-scope-violation",
                         "Agent Monitor is scoped to the active profile only")
    return active_root


def home_in_scope(home: Path) -> bool:
    """Whether ``home`` is the active profile's home (symlink-aware)."""
    active = hermes_home()
    try:
        return Path(home).resolve() == active.resolve()
    except OSError:
        return Path(home) == active


def allowed_homes(homes: Optional[Iterable[Tuple[str, Path]]] = None) -> List[Tuple[str, Path]]:
    """Clamp any caller-supplied homes to the active profile; a passed sibling cannot widen scope."""
    active = hermes_home()
    try:
        target = active.resolve()
    except OSError:
        target = active
    out: List[Tuple[str, Path]] = []
    actual = active_label()
    for _name, path in homes or []:
        candidate = Path(path)
        try:
            same = candidate.resolve() == target
        except OSError:
            same = False
        if same:
            out.append((actual, candidate))
    if out:
        return out
    return [(actual, active)]
