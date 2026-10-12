#!/usr/bin/env bash
# Script-only cron adapter: preserve stderr/exit status, suppress routine stdout.
set -euo pipefail
if [[ -n "${HERMES_BIN:-}" ]]; then
  cli="$HERMES_BIN"
elif command -v hermes >/dev/null 2>&1; then
  cli="$(command -v hermes)"
elif [[ -x /opt/hermes/bin/hermes ]]; then
  cli=/opt/hermes/bin/hermes
else
  printf 'quota collector: Hermes CLI unavailable\n' >&2
  exit 127
fi
exec "$cli" quota collect >/dev/null
