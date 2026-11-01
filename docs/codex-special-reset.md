# Codex special-reset announcements

The quota plugin can surface **public announcements about a Codex special
reset** (regular or banked) in its reset panels. This is announcement metadata
only. It is not account quota, it does not change any provider window or
`reset_at`, and it never claims that a reset was applied to the signed-in
account.

- Source: `GET https://codex-resets.com/api/v1/status` (public API).
- Data credit: the API asks consumers to link back. Any UI that displays this
  data should credit [Codex Resets](https://codex-resets.com).
- Implementation: `codex_special_reset.py`; cache write in `quota_cache.py`;
  CLI JSON exposure in `commands.py`.

## Cache contract

Both `$HERMES_HOME/quota_cache.json` and the `hermes quota status --json`
payload carry one additive root key:

```json
{
  "codex_special_reset": {
    "announcement": {
      "id": "2113162413008884099",
      "status": "scheduled",
      "reset_type": "regular",
      "announced_at": "2026-10-22T06:55:54+00:00",
      "scheduled_for": "2026-10-22T17:00:00+00:00",
      "source_url": "https://x.com/thsottiaux/status/2113162413008884099"
    },
    "fetched_at": "2026-10-22T10:00:00+00:00",
    "stale": false,
    "unavailable_reason": null
  }
}
```

- `announcement` is `null` when no valid explicit announcement is known.
- `status` is `scheduled` (explicit pending announcement) or `recorded`
  (latest recorded announcement). Scheduled wins when both are valid.
- `reset_type` is `regular` or `banked`.
- `announced_at` is the **publication time** of the announcement, not the time
  a reset ran or was applied.
- `scheduled_for` is the publisher's **estimate** of when the reset lands
  (`null` when the source does not publish one). It is displayed as
  "scheduled …", never as evidence that the reset ran; the record stays
  `status: scheduled`.
- `source_url` is always an allowlisted `https://x.com/thsottiaux/status/<id>`
  post from an `x_post` source. Observed-only records, forecasts and statistics
  are rejected.
- `fetched_at` is the last honest local success (HTTP 200 or 304), or `null`.
- `stale` is `true` when the entry is not a trustworthy current success; the
  last valid announcement is preserved with a reason.
- `unavailable_reason` is `null` on success, otherwise a short code such as
  `not-fetched`, `codex-not-configured`, `timeout`, `rate-limited`, `http-429`,
  `http-500`, `bad-json`, `schema`, `oversize`, `network-error`, `redirect`.

Consumers should tolerate a missing key or `null` announcement and must not
turn an announcement into a per-account reset event.

## Display

Desktop shows one provider-level notice in the route and docked pane (clean
and dense modes), plus the existing Codex status-bar tooltips (all and worst
modes). The links open the source announcement and credit Codex Resets.

The optional Ink TUI chip includes the announcement when it fits without
losing quota state. Below 60 columns it preserves the quota chip and shows
the announcement in `/quota-select` instead. Long labels or error states can
also make the chip fall back to quota only; the selector retains the notice.

## Refresh behavior

- The source is only queried when the Codex provider result reflects a
  configured account. A sweep without Codex never contacts codex-resets.com.
- The request is anonymous: fixed HTTPS endpoint, TLS verification, no
  credentials, cookies or browser session, no redirects and no environment
  proxies. The body is capped at 64 KiB and the timeout is 5 seconds.
- The announcement fetch runs in its own daemon thread and must finish inside
  the existing quota refresh wall-clock budget. A miss is recorded as
  `timeout`; it never extends the sweep. It waits only for Codex discovery,
  not unrelated providers. Its socket timeout is capped by the remaining
  sweep time (and never exceeds 5 seconds).
- `Cache-Control` is honored (including the current short `max-age`),
  `ETag`/`If-None-Match` is used, and a 304 renews `fetched_at`; `Retry-After`
  on 429 suppresses requests until the cooldown passes.
  A 429 without a usable cooldown receives a 60-second fallback backoff.
- Cached validators and the expiry window are bound to a digest of the exact
  normalized entry they describe. If the caller supplies any other entry (for
  example a newer fetch was discarded or superseded by another process), the
  expiry shortcut and `ETag` are not reused: the request is unconditional and a
  304 without a matching binding is reported as `http-304`, never as a renewal
  of the older entry.
- A direct 200 response must carry a JSON `Content-Type`; an HTML/error page is
  rejected as `content-type` instead of being parsed as JSON.
- Transport/parse failures are isolated: the last valid announcement is kept
  with `stale: true` and a reason, and provider quota is unaffected. A failed
  announcement fetch never breaks the cache or the CLI. Malformed explicit
  announcements are not mistaken for a fresh empty response; legitimate
  empty responses and intentionally excluded observations remain hidden.
- Cache metadata used only by the fetcher (entry digest, ETag, expiry, retry
  deadline) lives in `$HERMES_HOME/quota_special_reset_state.json`, separate
  from the public contract. The path is resolved on the caller's profile thread
  before the metadata worker starts, because thread context is not inherited.
  State is written through a unique secure temporary file and `os.replace`, so
  concurrent processes cannot collide on a fixed `.tmp` sibling.
- Cached/`--cached` reads never perform network I/O.

## Checks

```bash
python3 -m unittest discover -s tests -p 'test_codex_special_reset.py' -v
python3 -m unittest discover -s tests -q
```
