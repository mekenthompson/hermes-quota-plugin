# Shared-account collection compatibility

This fork starts from upstream `b0fadb0976465f4eab4cc77caf76990cd256221b` (3.0.0 plus the post-release Desktop fixes). It retains upstream provider/UI/cache and Agent Monitor code. Agent Monitor remains opt-in; do not enable it to obtain shared subscription history.

## One representative source per subscription

A credential owner runs `hermes quota sample --provider PROVIDER`. This performs a selected, nonpersisting fetch and emits allowlisted JSON. A separate existing controller combines representative samples and sends JSON on stdin to `hermes quota ingest`. No credentials or arbitrary vendor details cross that boundary. OpenRouter's validated USD wallet and allowlisted usage lines are preserved, not combined with percentage windows.

Enable `plugins.entries.quota.settings.managedCollection` on the central view through the supported Hermes configuration command. Local persisted refresh then refuses to replace the combined cache. Dashboard Reload and CLI cached status do not poll vendors. The upstream Desktop remains the primary presentation, with a collapsed shared-account history section backed by the canonical ledger. The retained web page is a compatibility surface, not another collector.

Claude extra-account configuration uses explicit stable ids, the owning profile's `claudeConfigDir`, and `verifyHistoryIdentity: true`. Account identity comes from the authenticated account/organization response, not a local display label. Codex grouping includes the effective request account scope. Grok retains its CLI billing subscription pool and native owner resolver; no new refresh-token writer or automatic browser-cookie fallback is introduced.

## Freshness and history

The central sweep is conservatively dated at the oldest source fetch. Per-record `fetched_at` and `observed_at` distinguish source fetch time from cached observation time. Explicit stale/errors and invalid/old observation times cannot become healthy history. Sources must be validated within two minutes by the controller. Plans and vendor-declared window durations survive the exchange. Labels and distant resets do not establish a duration.

The existing `quota_history.sqlite3` remains canonical, retaining 90 days. The legacy nine-column `samples` schema is unchanged. A companion `sample_metadata` table in the same database stores nullable plan, observation time, source fetch time and declared duration. Old observations remain unknown; they are not backfilled, merged by display labels, or reconstructed. Old writers can still insert into `samples` after rollback.

`hermes quota history --json --days 90` reads this ledger. `hermes quota forecast --json` estimates recent continuous pace. A missing window, failure, identity uncertainty, reset change, percentage decrease, plan change or declared-duration change breaks continuity. Estimates are conditional, not vendor guarantees.

Completed-week summaries use ended, vendor-declared 604800-second cycles. They expose coverage, partial-cycle, failed-sample, saturation and discontinuity flags. Only verified identities with one known plan and adequate continuous coverage contribute to averages. Each eligible cycle contributes one final observed percentage, not hundreds of hourly snapshots. The last observation before reset is an estimate, not a measured vendor total.

## Delivery and rollback

Install the independently reviewed fork at an exact 40-character Git commit using the native plugin installer `--ref` option. Keep the fork source URL and pin in install metadata; do not use a moving update for a controlled delivery. Verify the actual loaded backend and Desktop bytes separately from metadata.

Before activation, preserve source/config/scheduler preimages and use SQLite's backup API for a consistent history backup. Preserve the existing representative schedule, its no-agent execution and failure route. The controller must ingest unavailable markers before reporting required-provider health failure; native scheduler incidents provide deduplicated outage handling and recovery resolution. Storage failure is distinct from provider failure.

Rollback restores prior exact plugin and collector/config bindings without restoring an old history file over newer samples. The added metadata table can remain. No gateway restart, credential copying, model/default change or second polling schedule is required by this source change.

## Qualification boundaries

Offline tests cover real handlers, render components, sanitizers and SQLite, not a deployed frontend. Deployment acceptance still requires exact loaded-source readback, authenticated cards, fresh distinct subscription readings, retained history and one genuine scheduled collection. Unknown OpenAI reset/plan discontinuities must remain unexplained until vendor evidence establishes their cause.
