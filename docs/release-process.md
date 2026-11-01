# Release process

One path for every change: branch → gates → review → merge → version → tag →
notes → catalog. A small, tested diff may be merged directly into `master`
without a pull request; anything touching credentials, the cache contract or the
Desktop SDK gets a PR and an independent review.

## 1. Branch and scope

- Branch from `master`: `fix/<slug>` or `feat/<slug>`.
- One behavior per branch. The version bump and the docs go in their own commit.
- Do not mix a provider change with a TUI change in the same branch.

## 2. Gates

Run every gate on the exact tree that will be merged:

| Area | Command |
| --- | --- |
| Backend syntax | `python -m py_compile __init__.py commands.py quota_cache.py quota_providers/*.py` |
| Backend tests | `python -m unittest discover -s tests -p "test_*.py"` |
| Widget syntax | `node --check desktop/plugin.js && node --check tui/quota-panel.mjs` |
| TUI tests | `node --test tests/tui-panel.test.mjs` |
| Install scanner | `python scripts/scan_plugin.py` |
| Host admission | `hermes plugins doctor <tree> --ci` |
| TUI behavior | PTY probe against a synthetic `HERMES_HOME`, never the live one |
| Cache behavior | `hermes quota refresh`, then read `quota_cache.json` back |
| Install smoke | default profile only; read back `version.json` and the widget hash |

Baseline rule: record the failures that already exist on `master` before
blaming the branch. A green CI run is not evidence for the TUI, the install path
or the live cache.

## 3. Review

- Independent review (`agy`) for behavior, security or credential changes;
  record the verdict and the reviewed SHA.
- A docs-only change or a self-contained test does not need one.

## 4. Merge

- `git merge --no-ff <branch>` into `master`.
- Push `master`. The branch may stay for history.

## 5. Version

- Semver: patch = bug fix, minor = new provider or feature, major = breaking
  config or cache contract.
- Bump `plugin.yaml` in a `chore(release): X.Y.Z` commit, never inside the
  behavior commit. Bump `desktop/plugin.js` `WIDGET_VERSION` in the same commit:
  `tests/test_widget_version.py` fails on a skew, and 2.11.1 shipped one.

## 6. Release notes and tag

- Add the entry to `CHANGELOG.md`.
- Annotated tag whose message is the release note:

  ```bash
  git tag -a vX.Y.Z -m "Hermes Quota X.Y.Z" -m "- bullet"
  ```

- Push `master` and the tag together, then verify:

  ```bash
  git ls-remote --tags origin vX.Y.Z
  ```

## 7. Catalog and local install

- Re-pin `catalog/quota.yaml` to the tagged commit and bump its `version`.
  The catalog draft is local; its PR is a separate publication step and needs an
  explicit GO.
- Reinstall locally and confirm the new hashes. The widget needs
  `/widgets-reload` (or a TUI restart); the Python command needs a gateway
  restart, which cannot be done from inside a running gateway.
