#!/usr/bin/env bash
# One place that answers "is everything fine?" across the homelab.
#
#   ./status.sh            collect the fast signals, render, print the summary
#   ./status.sh --fast     health, containers, deploy drift, backups, vault
#   ./status.sh --slow     test suites and outdated packages (minutes)
#   ./status.sh --print    print the last snapshot without collecting
#   ./status.sh --html     re-render status.html from the last snapshot
#
# Runs on the Mac, and has to: the source-side signals (tests, unpushed work,
# outdated packages, the commit a running image was built from) exist nowhere
# else, and the NAS host cannot reach its own macvlan children to probe them.
#
# Nothing here fails the run because one app is down — a collector that aborts
# on the first problem is useless on the day it is needed. Every section carries
# its own timestamp so a page that was not refreshed reads as stale rather than
# as healthy.
#
# The collectors are in status/; this is the entry point, named like
# vault-sync.sh and backup-vault.sh beside it.
set -euo pipefail

cd "$(dirname "$0")"
# launchd hands a minimal PATH, where `python3` resolves to Xcode's 3.9 — which
# has no datetime.UTC, so every scheduled run died with an ImportError while
# interactive runs worked fine. Resolve a real interpreter explicitly and say so
# loudly rather than discovering it in a log nobody reads.
pick_python() {
  local c
  for c in "${PYTHON:-}" python3.14 python3.13 python3.12 python3.11 \
           /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    [ -n "$c" ] || continue
    if command -v "$c" >/dev/null 2>&1 &&
       "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      command -v "$c"; return 0
    fi
  done
  echo "✗ no python >= 3.11 found (checked PYTHON, python3.11-3.14, homebrew, python3)" >&2
  exit 3
}
PY="$(pick_python)"
DIR="${HOMELAB_STATUS_DIR:-$HOME/.homelab/status}"

# Both LaunchAgents append here, ~1 KB a run, 48 runs a day. Left alone that is
# a log growing forever to hold a summary nobody reads twice, so keep the tail.
LOG="${DIR}/collect.log"
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 262144 ]; then
  tail -n 400 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
fi

case "${1:-}" in
  --slow)  exec "$PY" status/collect.py --slow ;;
  --print) exec "$PY" status/summary.py ;;
  --html)  exec "$PY" status/render.py ;;
  --fast|"") ;;
  *) echo "usage: $0 [--fast|--slow|--print|--html]" >&2; exit 2 ;;
esac

"$PY" status/collect.py --fast >/dev/null
"$PY" status/render.py >/dev/null
"$PY" status/summary.py
echo
echo "  page: ${DIR}/status.html"
echo "  publish: ask Claude to refresh the status page (a LaunchAgent cannot)"
