#!/usr/bin/env bash
# Report drift between homelab/deploy.lib.sh and the copies vendored into each
# project.
#
# The four repos are independently published and deliberately share no code, so
# the library is COPIED rather than depended on. That keeps each repo standing
# alone — at the cost that a fix made in one copy is invisible to the others.
# This is what makes it visible: run it after touching any copy.
#
#   ./vendor-check.sh          report drift, exit 1 if any
#   ./vendor-check.sh --sync   overwrite every project copy from this one
set -euo pipefail

cd "$(dirname "$0")"
CANON="deploy.lib.sh"
# Projects live as siblings of homelab/ in the same parent directory.
PROJECTS="family_calendar podcast-digest security-digest taster clippings-topics vault-ask video-digest"

SYNC=false
[ "${1:-}" = "--sync" ] && SYNC=true

drift=0
missing=0
for p in $PROJECTS; do
  copy="../${p}/${CANON}"
  if [ ! -d "../${p}" ]; then
    echo "  ? ${p}: no such directory next to homelab/ — skipped"
    continue
  fi
  if [ ! -f "$copy" ]; then
    if $SYNC; then
      cp "$CANON" "$copy"; chmod +x "$copy"
      echo "  + ${p}: vendored (was missing)"
    else
      echo "  ✗ ${p}: ${CANON} is MISSING"; missing=1
    fi
    continue
  fi
  if cmp -s "$CANON" "$copy"; then
    echo "  ✓ ${p}: in sync"
  elif $SYNC; then
    cp "$CANON" "$copy"; chmod +x "$copy"
    echo "  ↻ ${p}: updated from homelab/"
  else
    echo "  ✗ ${p}: DRIFTED —"
    diff -u "$CANON" "$copy" | sed -n '3,$p' | sed 's/^/      /' | head -40
    drift=1
  fi
done

if $SYNC; then
  echo
  echo "Synced. Commit each project's ${CANON} separately — they're separate repos."
  exit 0
fi

if [ "$drift" = 1 ] || [ "$missing" = 1 ]; then
  echo
  echo "Fix the bug in homelab/${CANON}, then: ./vendor-check.sh --sync"
  echo "(If a project's copy is the one that's right, port the fix here FIRST —"
  echo " --sync overwrites the project copies, it never reads back from them.)"
  exit 1
fi
