#!/usr/bin/env bash
# Back up the vault's CouchDB database — the `the_brain` db every app's
# VAULT_* variables point at, and the one thing here that actually matters:
# LiveSync copies on any device are sync artifacts, not backups (see
# taster/INSTALL.md and tasting-log-design.md §7). Losing this loses every
# note, every topic page, every tasting record — everything.
#
# Runs from the Mac, not the NAS: the vault's CouchDB is LAN-reachable
# directly (unlike podcast-digest's own state DB, which is intentionally
# loopback-only), and running the dump here means the backup exists off the
# NAS from the moment it's created — a single NAS disk failure can't take out
# both the live data and its backup together. Modelled on
# podcast-digest/scripts/backup.sh, pointed at the vault instead of that
# app's own database.
#
# Usage:
#   ./backup-vault.sh [output-dir]
#
# Reads VAULT_COUCHDB_URL / VAULT_DB / VAULT_USER / VAULT_COUCHDB_PASSWORD /
# VAULT_BACKUP_DIR from .env (see .env.example) so nothing real lives in this
# tracked file. VAULT_DB defaults to `the_brain`, but a caller that already
# exported VAULT_DB (the hobby-vault LaunchAgent, say) wins over .env's own
# value -- both databases share the same server/admin creds, so only the
# database name needs to differ between the two scheduled backups.

set -euo pipefail

cd "$(dirname "$0")"
_preset_vault_db="${VAULT_DB:-}"
[ -f .env ] && set -a && . ./.env && set +a
[ -n "$_preset_vault_db" ] && VAULT_DB="$_preset_vault_db"

OUT_DIR="${1:-${VAULT_BACKUP_DIR:-$HOME/Backups/vault-couchdb}}"
COUCHDB_URL="${VAULT_COUCHDB_URL:?Set VAULT_COUCHDB_URL in .env}"
COUCHDB_USER="${VAULT_USER:?Set VAULT_USER in .env}"
COUCHDB_DB="${VAULT_DB:-the_brain}"
KEEP="${KEEP:-14}"

if [ -z "${VAULT_COUCHDB_PASSWORD:-}" ]; then
  echo "FATAL: VAULT_COUCHDB_PASSWORD is not set (check .env)" >&2
  exit 2
fi

mkdir -p "$OUT_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TMP="$OUT_DIR/.${COUCHDB_DB}-${STAMP}.json.gz.tmp"
FINAL="$OUT_DIR/${COUCHDB_DB}-${STAMP}.json.gz"

echo "Backing up ${COUCHDB_URL}/${COUCHDB_DB} -> ${FINAL}"

# include_docs gives full bodies; attachments=true inlines LiveSync's chunk
# data (and taster's tasting-photo blobs) as base64 so nothing is silently
# dropped from the dump.
curl -fsS --max-time 900 \
  -u "${COUCHDB_USER}:${VAULT_COUCHDB_PASSWORD}" \
  -H 'Accept: application/json' \
  "${COUCHDB_URL}/${COUCHDB_DB}/_all_docs?include_docs=true&attachments=true" \
  | gzip -9 > "$TMP"

# Verify before publishing: a truncated backup that looks fine is worse than
# an obvious failure, because it's only discovered when it's needed.
DOC_COUNT="$(gzip -dc "$TMP" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
rows = payload.get("rows")
if rows is None:
    print("no rows key in response", file=sys.stderr)
    raise SystemExit(1)
if len(rows) == 0:
    print("zero rows — refusing to publish an empty dump as a backup", file=sys.stderr)
    raise SystemExit(1)
print(len(rows))
')"

mv "$TMP" "$FINAL"
echo "OK: ${DOC_COUNT} documents, $(du -h "$FINAL" | cut -f1)"

# Rotate, newest first.
if [ "$KEEP" -gt 0 ]; then
  # shellcheck disable=SC2012 — filenames are timestamped and shell-safe.
  ls -1t "$OUT_DIR/${COUCHDB_DB}-"*.json.gz 2>/dev/null \
    | tail -n +$((KEEP + 1)) \
    | while read -r old; do
        echo "Rotating out $(basename "$old")"
        rm -f "$old"
      done
fi
