# Agent Monitor in Hermes Quota 3.0

Agent Monitor adds live Hermes sessions, attention indicators, model health,
usage estimates, quota history and burn-rate forecasts inside Desktop. It uses
the gateway's `cli.exec` path, not a separate monitoring server. Monitor commands
read local data; quota fetching remains in the existing provider refresh path.

## Enrollment and scope

Collection ships **off by default**, independently of the existing quota view.
Run the supported command on the backend's machine for the intended profile:

```bash
hermes config set plugins.entries.quota.settings.monitorEnabled true
# Named profile:
hermes -p <profile> config set plugins.entries.quota.settings.monitorEnabled true
```

Set the same value to `false` to stop collection. This does not erase existing
history. Only the literal YAML boolean `true` counts as enrollment, not strings
or numbers. Without enrollment, monitor readers/store operations refuse access
with `monitor-enrollment-off`; the enrollment check itself reads configuration.

3.0 retains the read-only trial boundaries. The broader [proposal #83](https://github.com/rarf/hermes-quota-plugin/issues/83)
is not a list of capabilities all enabled in this release.

| Capability | Shipped behavior |
| --- | --- |
| Hermes sessions, messages, usage | Active profile only; SQLite read-only |
| Active session leases | Active profile runtime registry |
| Model health and fallback visibility | Local profile configuration and recorded activity/errors |
| History and forecasts | Existing quota cache plus monitor history; sufficient readings required |
| Context guard | Local estimates; `/compress` uses the Desktop composer path |
| Usage costs | Estimated API-price equivalents, not invoices or billed charges |
| Month-end spending projection | Unavailable; no projected amount rendered |
| Sibling profiles | Not collected |
| External CLI transcripts (Claude Code, Copilot, etc.) | Collection disabled |
| Shared Kanban boards | Collection disabled |
| New credential-pool metadata reads | Unavailable; monitor does not read auth.json or credential stores |
| Account priority/strategy and budget writes | Refused with `trial-read-only` |
| Monitor agent tools | Not registered; direct calls refuse with `trial-tools-disabled` |

Unavailable tabs do not enable these capabilities. Unavailable account metadata
is not evidence of an empty credential pool. Machine-readable `trial-*` codes
remain for compatibility with the evaluated candidate.

These boundaries apply to the **new monitor**. Existing quota providers retain
their own credential behavior and opt-ins; see [provider disclosures](providers.md)
and [security limitations](security.md). Python plugins are trusted host code,
not sandboxed applications.

## Commands

```bash
hermes quota monitor snapshot --json
hermes quota monitor history --json --days 7
hermes quota monitor recommend --json
hermes quota monitor budget --json
hermes quota monitor accounts --json
```

Payloads carry `monitor_version` and `generated_at`. The monitor schema version
remains `1`, independently of the plugin release version. Snapshot sections fail
independently; inspect `errors` and `timings_ms` instead of interpreting missing
data as zero activity. Recommendations are advisory and do not switch models.
`set-budget`, `set-priority` and `set-strategy` are unavailable in this release.

## Storage and estimates

The monitor records its own history and sampling data in the active profile's
`quota_monitor.db`. The database is private (`0600`); creation uses a private
parent, rejects database/sidecar symlinks, and uses WAL with a busy timeout.
Read paths do not create the database or run DDL. This is filesystem hardening,
not isolation from other trusted code running as the same OS user.

Quota history comes from existing cache readings. Identical consecutive readings
are deduplicated, with periodic retention. History is pruned after 35 days and
rate samples after one hour. Forecasts estimate a trend from recent readings;
resets, sparse data and changing usage can make them unavailable or inaccurate.
They are not provider guarantees.

Context uses local usage anchors and model metadata. Transcript-length estimates
are approximate; unknown model limits stay unknown without a network probe.

## Desktop and remote gateways

The widget runs on the Desktop computer; the backend runs on the selected
gateway/profile. Update both halves separately. Installing a Mac widget does
not update a Linux backend. Replacing backend files does not prove a running
gateway reloaded them; inspect version warnings and fresh runtime output.

The localized monitor UI includes English, Turkish, German, Spanish, French,
Russian, Japanese, Simplified Chinese, Traditional Chinese and Arabic (RTL).
Native notifications depend on SDK support, OS permission and app settings.
Missing platform capabilities degrade to unavailable/empty values. The original
standalone app's native menu-bar/notch widgets are not included.

## Attribution

Thank you to [@Asimatasert](https://github.com/Asimatasert) for the
[agent-monitor](https://github.com/Asimatasert/agent-monitor) contribution and
follow-up fixes. See [Acknowledgements](contributors.md).
