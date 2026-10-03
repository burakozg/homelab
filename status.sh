#!/usr/bin/env bash
# One place that answers "is everything fine?" across the homelab.
#
#   ./status.sh            Mac-side checks, push them to the NAS, pull the live
#                          signals back, render, print the summary
#   ./status.sh --fast     the same (containers, revisions, config drift)
#   ./status.sh --slow     test suites and outdated packages (minutes)
#   ./status.sh --print    pull the NAS's live signals, print the summary
#   ./status.sh --html     pull the NAS's live signals, re-render status.html
#   ./status.sh --publish  push the architecture/models pages from the last snapshot
#
# Split in two, by what each side can see:
#   NAS (nas-jobs/, a container) — health probes, backups, the vault: anything
#     that needs only the network. It runs whether or not the laptop is awake,
#     and renders the status page the governance site serves.
#   Mac (this script) — what only exists here: test suites, unpushed work,
#     outdated packages, and the commit each running image was built from.
#
# Nothing here fails the run because one app is down — a collector that aborts
# on the first problem is useless on the day it is needed. Every section carries
# its own timestamp so a page that was not refreshed reads as stale rather than
# as healthy.
#
# The collectors are in status/; this is the entry point, named like
# vault-sync.sh beside it.
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

# Push the Architecture and Models pages (governance/) to the NAS — the only
# governance pages the Mac still renders. A failure here must not fail the
# collection, but must not be silent either: the site's own staleness banner is
# the backstop, this line is the early warning.
publish_governance() {
  [ -f governance/.deploy.env ] || return 0
  local out
  # PYTHON: launchd's PATH resolves python3 to Xcode's 3.9, see pick_python.
  if out="$(PYTHON="$PY" governance/deploy publish 2>&1)"; then
    echo "  published: governance site"
  else
    echo "  ✗ governance publish failed — the site will go stale:"
    printf '%s\n' "$out" | tail -n 3 | sed 's/^/    /'
  fi
}

# Hand the Mac's snapshots to the NAS so its page includes them. Best effort: the
# NAS being unreachable must not lose the local result.
push_to_nas() {
  [ -f nas-jobs/.deploy.env ] || return 0
  local out
  if ! out="$(PYTHON="$PY" nas-jobs/deploy push 2>&1)"; then
    echo "  ✗ could not push to the NAS — its page will show these checks as stale:"
    printf '%s\n' "$out" | tail -n 3 | sed 's/^/    /'
  fi
}

# Fetch the NAS's live.json. Without it the summary falls back to whatever the
# Mac last had, and says how old it is.
pull_from_nas() {
  [ -f nas-jobs/.deploy.env ] || return 0
  "$PY" status/collect.py --pull >/dev/null 2>&1 || echo "  ✗ could not pull live signals from the NAS (showing the last copy)" >&2
}

case "${1:-}" in
  --slow)
    "$PY" status/collect.py --slow
    push_to_nas
    publish_governance
    exit 0 ;;
  --print) pull_from_nas; exec "$PY" status/summary.py ;;
  --html)  pull_from_nas; exec "$PY" status/render.py ;;
  --publish) pull_from_nas; "$PY" status/render.py >/dev/null; publish_governance; exit 0 ;;
  --fast|"") ;;
  *) echo "usage: $0 [--fast|--slow|--print|--html|--publish]" >&2; exit 2 ;;
esac

"$PY" status/collect.py --fast >/dev/null
push_to_nas
pull_from_nas
"$PY" status/render.py >/dev/null
"$PY" status/summary.py
echo
echo "  page: ${DIR}/status.html"
