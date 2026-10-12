# Quota history and scheduled collection

`hermes quota collect` fetches a fresh provider snapshot and commits a sanitized
observation to `$HERMES_HOME/quota_history.sqlite3`. It is silent on success and
fails nonzero on history storage failure. It uses no language model. Ordinary
`quota refresh` continues to update the latest display cache only.

History retains up to 90 days, relative to the newest observed sample. It stores
fetch time, provider, pseudonymous account identity when available, display
label, window/reset time, percentage used and an availability status. It does
not store tokens, vendor response bodies or detail strings. A retry of the same
sample is idempotent; concurrent collectors are serialized by SQLite.

Read it with:

```
hermes quota history
hermes quota history --json --days 30
hermes quota history --provider grok
```

The dashboard exposes `/api/plugins/quota/history` and an observed-daily-changes
section. The Python API may need a coordinated dashboard reload after updating
an already-mounted plugin API. Reloading a chat command alone is not proof that
the dashboard has loaded a new route.

Daily changes are **observed percentage-point deltas**, not vendor-provided daily
usage totals. Days are UTC. Deltas crossing an error, a gap longer than two
hours, a percentage decrease or a changed reset are excluded. Unknown account
identity and missing reset times are not joined into trends. There is no
run-out forecast yet and no retrospective reconstruction of previous usage.

Grok and Codex derive a pseudonymous hint from stable JWT subject/account claims,
not from the access token. It is a grouping hint, not authentication proof.
Opaque-token providers, including Claude where no stable claim is exposed, are
stored as unverified observations and are not silently combined into a trend.

## Hourly operation

Use `scripts/collect-hourly.sh` with the scheduler's script-only/no-agent mode.
The adapter preserves stderr and exit status while suppressing routine stdout.
It resolves `HERMES_BIN` when explicitly set, then `hermes` on PATH, then the
standard `/opt/hermes/bin/hermes` installation. A missing CLI fails nonzero.
Schedule once per unique subscription account, using a representative profile
that already owns its credentials. Do not copy credentials between profiles or
poll the same shared subscription from every agent. Collection continues while
Desktop/dashboard are closed, provided the scheduler gateway is running.

Create the job paused, verify one collection and durable history readback,
exercise the real scheduler path, and only then enable the hourly schedule.
Profile-local history does not create a fleet-wide shared account ledger: other
profiles need an explicitly authorized read-only aggregation path to see it.
