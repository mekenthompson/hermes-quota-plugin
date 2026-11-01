# Quota panel for the Hermes Ink TUI

This optional widget adds a compact quota bar above the composer.
It requires a Hermes build with the user-widget SDK (`hermes --tui`); it does
not render in the classic `hermes --cli` interface or messaging platforms.
No Hermes core patch, model change or extra npm dependency is needed.

## Install into one profile

Copy `tui/quota-panel.mjs` from this repository to
`$HERMES_HOME/tui-widgets/quota-panel.mjs`. For the default profile, HERMES_HOME
normally resolves to `~/.hermes`. Do not copy into other profiles implicitly.
The existing Python Quota plugin and `hermes` executable must be installed.
This optional widget is not wired into the repository's multi-profile installer.

The TUI watches the widget directory. If the command is missing, run
`/widgets-reload` and check that `quota-panel.mjs` appears under `loaded:`.
Installing the file changes nothing by itself: the dock opens only when you ask
for it, and only then does it remember that choice (see
[Saved preference](#saved-preference-start-the-tui-with-the-dock-already-open)).

## Commands

- `/quota-select`: open the interactive provider/account picker.
- `/quota-panel`: toggle the dock; on first use, open the picker.
- `/quota-panel anthropic:account2`: directly show one account ID.
- `/quota-panel off`: stop auto-opening the dock in future TUI sessions.
- `/quota-panel on`: reopen the dock with the remembered account.
- `/quota`: existing full detail command, unchanged.

Use Up/Down and Enter to choose, Esc/q/Ctrl-C to cancel. Selecting an account
opens or replaces the dock without changing inference configuration. The picker
lists cached rows except `no-credentials`, `disabled`, `not-enabled`,
`not-opted-in` and `opt-in-disabled`; unavailable rows with other errors remain
selectable and visibly marked. This is cached availability evidence, NOT proof
that a key is currently valid. No credential files are opened by the picker.
Missing/invalid cache produces instructions to run `/quota refresh`, not a
fabricated account list. Stale cache is explicitly marked in the picker.

Only one provider/account is displayed. With a direct argument, the selection
is replaced and the panel stays open. Without arguments, an open panel closes.
IDs are case-sensitive; automatic current-inference-account detection is not
claimed. A direct provider ID with nested accounts shows its first matching
account; use the picker to choose a specific nested account.

## Saved preference (start the TUI with the dock already open)

Showing the dock writes `$HERMES_HOME/quota-panel.state.json`, next to
`quota_cache.json`:

```json
{ "version": 1, "selected": "anthropic:account2", "autoOpen": true }
```

On the next TUI start the saved account opens automatically, before any
command — the dock is simply there above the composer. Picking an account in
the picker or naming one on the command line updates `selected`; mounting the
dock sets `autoOpen: true`.

A plain `/quota-panel` still closes the dock for the current session; because
`autoOpen` stays true, it returns on the next start. To stop that, run
`/quota-panel off` (or set `autoOpen: false` / delete the file). `off` only
changes the startup preference, so the visible dock is closed with a following
`/quota-panel`. `/widgets-reload` and other hot reloads never reopen a dock that
was closed in the running session.

The file deliberately lives OUTSIDE `tui-widgets/`: the TUI reloads every widget
when that directory changes, so a preference written there would remount the
dock and write again. It is written atomically, mode 0600, only when a value
actually changes, and holds nothing else: no credentials, no tokens, no
inference-model or provider configuration. Anything unreadable, oversized,
malformed or with an invalid selector falls back to "no saved dock". A read-only
`HERMES_HOME` silently keeps the dock session-only. The preference is per
profile, because it lives under that profile's `HERMES_HOME`.

The dock uses `dock-top`, verified above the composer in the real layout. It
occupies exactly one right-aligned row: explicit `Quota` label, friendly
provider/account name, labelled window, remaining-capacity bar/percentage and
reset. When an OpenAI Codex account is selected and a validated special-reset
announcement exists, the same row also carries it — announced date and time in
the user's configured timezone, status and the `codex-resets.com` credit — as
long as it still fits; below 60 columns the
chip stays pure quota and the picker shows the notice instead. Forecasts,
observations and unvalidated sources are never displayed. See
[Codex special-reset announcements](codex-special-reset.md).
It has no background strip or rule, distinguishing it from the native
status bar. Display names match Desktop in both
the picker and the dock; machine IDs remain stable for command arguments.
The tightest known window is shown with a short bar; `+N` indicates additional
windows (use `/quota` for details). No independent allowances are summed.

Reset uses a compact local absolute date/time, e.g. `reset 27/10 14:30`.
Across years the year is included. Routine cache age and refresh activity are
hidden to reduce noise; STALE and refresh-error markers remain explicit.
As width decreases the bar and name shorten. Below 60 usable columns reset is
omitted; below 49 the window label and word `left` are omitted. The additional-
window count is retained. Full reset/window details remain in `/quota`.
Host chrome padding is reserved so the last columns are not clipped.

The `Quota` label uses theme primary/bold; bar and percentage use theme ok/warn/
error by remaining capacity; names, separators and reset use theme muted. No
hardcoded color or background is applied. Bar fill is remaining capacity.
Unknown and stale values are muted; unknown values never produce a filled bar.
Balances remain money, never quota percentages. `% left` is remaining capacity,
not usage. `? left` means unknown. `reset due` is not proof of renewed capacity.

## Refresh and safety

The component reads only the active profile's `quota_cache.json`, immediately
on mount and every 15 seconds. While mounted, a cache older than 10 minutes (or
missing/invalid) triggers the existing `hermes quota refresh` in the background.
Only one refresh runs per mounted panel. Failed refreshes retry no sooner than
60 seconds; subprocesses have a 45-second timeout. Closing unmounts the poller
and aborts its refresh process. Nothing runs while the panel is closed.

The widget has no credential reader and never changes configuration or models.
Refresh delegates to the already-installed plugin and its enabled providers,
including that plugin's existing authentication behavior. Provider selection
filters the display only; it does not narrow the existing collector's sweep.

The single row prioritizes STALE and refresh-error markers while refreshing or
reporting an error. Unavailable providers are not presented as zero usage/full capacity.
Cache timestamps describe the collector's snapshot, not a fresh direct API read.

## Verification and rollback

`node --check tui/quota-panel.mjs`

`node --test tests/tui-panel.test.mjs`

For an interactive check, load the widget, open it, type without submitting a
normal message, then use `/quota-panel` to close. The composer must remain usable.
Restarting the TUI must bring the dock back; `/quota-panel off` must keep it
closed in the next process.

To disable, close the panel and move only its `.mjs` file out of the profile's
`tui-widgets` directory, then run `/widgets-reload`. Preserve the file for rollback;
no cache, credentials or other plugin files need to be removed. The optional
`$HERMES_HOME/quota-panel.state.json` may be deleted freely — it only remembers the
display preference.
