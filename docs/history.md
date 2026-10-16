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
identity and missing reset times are not joined into trends. No previous usage
is reconstructed retrospectively.

## Period forecasts and shared accounts

`hermes quota forecast --json` and `/api/plugins/quota/forecast` read stored
observations only. The dashboard shows used/remaining percentages, reset time,
observed percentage points per hour, projected demand by reset and estimated
exhaustion. **Each account already reflects every client spending its quota.**
Do not add percentages from different agents, accounts or windows together.

The estimate assumes recent observed pace continues, not that future workload
is known. It uses at most 24 hours of continuous samples, requires three samples
spanning at least two hours, and never bridges errors, missing windows, changed
resets, percentage decreases or gaps longer than two hours. Stale, unknown,
reset-due and insufficient states are explicit. Zero measured burn is not a
promise of enough future quota. Demand above 100% means the projected workload
would exceed the allowance. Exhaustion after reset is conditional arithmetic,
not a forecast of usage in the next period.

For isolated credential owners, `hermes quota sample --provider grok` emits a
sanitized snapshot without changing local cache/history. Repeat `--provider`
for a bounded provider subset; unselected providers are not polled. Transfer
only that quota payload, never credentials, to a trusted controller, then feed
one combined, current sweep to `hermes quota ingest` on stdin. Ingest validates
and strips unknown fields, records history idempotently and updates the display
cache. It performs no vendor requests. Keep errors in the sweep instead of
reusing old successful values. A trusted orchestrator must enforce source
freshness, account coverage and one representative source per account. It must
not forge an old sample's timestamp to make it look current.

Grok and Codex derive a pseudonymous hint from stable JWT subject/account claims,
not from the access token. It is a grouping hint, not authentication proof.
Claude extra accounts may explicitly opt in to `verifyHistoryIdentity: true`.
This adds one bounded read of `/api/oauth/profile` with the exact token used for
the quota read. Both authenticated account and organization UUIDs are required;
only their domain-separated hash is stored. Missing/failed profile identity
remains unverified; local `.claude.json` metadata cannot establish the binding.

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
