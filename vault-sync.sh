#!/usr/bin/env bash
# Push the homelab projects' main docs into the Obsidian vault.
#
#   ./vault-sync.sh           write anything that differs
#   ./vault-sync.sh --check   report what would change, write nothing
#
# The vault is a reference copy, not a source: edit the doc in its repo and rerun
# this. Synced notes carry a `source:` line in their frontmatter saying where
# they came from, which is also how this script and you can tell them apart from
# the hand-written notes alongside them.
#
# HAND-WRITTEN NOTES ARE NEVER TOUCHED. Only the paths in the manifest below are
# written, so anything else in the vault — the IP map in "30 projects/README.md",
# per-project scratch notes — is safe. If you want one of those managed, add it
# here; if you want a managed one back under your own control, remove it here and
# delete its frontmatter.
#
# Deliberately NO timestamp in the header: LiveSync would otherwise replicate
# every note on every run even when nothing changed. Unchanged docs must
# produce byte-identical files.
set -euo pipefail

cd "$(dirname "$0")"
PROJECTS="$(cd .. && pwd)"
# The vault moved off iCloud during the 2026-08-31 LiveSync-only migration (see
# homelab/README.md) — it is now a plain local folder, not under Mobile
# Documents. Override with OBSIDIAN_VAULT if it lives elsewhere on this Mac.
VAULT="${OBSIDIAN_VAULT:-$HOME/Documents/obsidian/the_brain}"
DEST="${VAULT}/30 projects"

CHECK=false
ADOPT=false
case "${1:-}" in
  --check) CHECK=true ;;
  --adopt) ADOPT=true ;;
  "")      ;;
  *)       echo "usage: $0 [--check|--adopt]" >&2; exit 2 ;;
esac

BACKUP="${HOME}/obsidian-vault-presync-backup"

[ -d "$VAULT" ] || { echo "✗ Vault not found: ${VAULT}" >&2
                     echo "  Set OBSIDIAN_VAULT if it lives elsewhere." >&2; exit 1; }

# repo-relative source  →  vault-relative destination.
#
# The compose files come from deploy-out/, not the tracked template: those are
# rendered with the real addresses and MACs, which is what makes them useful as
# an ops reference. They are produced by `./deploy render` (or any ship) and are
# git-ignored, so a missing one is skipped with a note rather than an error.
MANIFEST="
family_calendar/README.md|family-calendar/README.md
family_calendar/ARCHITECTURE.md|family-calendar/ARCHITECTURE.md
family_calendar/DEPLOY.md|family-calendar/DEPLOY.md
family_calendar/RUNBOOK.md|family-calendar/RUNBOOK.md
family_calendar/deploy-out/docker-compose.nas.yml|family-calendar/docker-compose.yaml.md
security-digest/README.md|daily-digests/README.md
security-digest/deploy-out/docker-compose.nas.yml|daily-digests/docker-compose.yaml.md
taster/README.md|taster/README.md
taster/ARCHITECTURE.md|taster/ARCHITECTURE.md
taster/INSTALL.md|taster/INSTALL.md
taster/deploy-out/docker-compose.yml|taster/docker-compose.yaml.md
podcast-digest/README.md|podcast-digest/README.md
podcast-digest/architecture.md|podcast-digest/architecture.md
podcast-digest/DEPLOY-NAS.md|podcast-digest/DEPLOY-NAS.md
podcast-digest/deploy-out/docker-compose.nas.yml|podcast-digest/docker-compose.yaml.md
homelab/README.md|homelab/README.md
clippings-topics/README.md|clippings-topics/README.md
video-digest/README.md|video-digest/README.md
video-digest/deploy-out/docker-compose.nas.yml|video-digest/docker-compose.yaml.md
vault-ask/README.md|vault-ask/README.md
vault-ask/OLLAMA-SETUP.md|vault-ask/OLLAMA-SETUP.md
vault-ask/deploy-out/docker-compose.nas.yml|vault-ask/docker-compose.yaml.md
shortlist/README.md|shortlist/README.md
shortlist/deploy-out/docker-compose.nas.yml|shortlist/docker-compose.yaml.md
"

# The real addresses for a project, from its git-ignored .deploy.env.
#
# The repo docs carry PLACEHOLDERS by design — they are published, and the
# never-track-real-values rule applies. That makes them a poor ops reference on
# their own, so each note gets the actual values prepended instead of trying to
# substitute them into the prose, which would be fiddly and could produce a doc
# that is subtly wrong rather than obviously generic.
deploy_facts() {
  # Two statements, not one `local a=.. b=$a`: referencing a variable being
  # declared in the same `local` is not reliable, and under `set -u` it aborts.
  local proj="$1"
  local envfile="${PROJECTS}/${proj}/.deploy.env"
  [ -f "$envfile" ] || return 0
  local any=false line k v
  while IFS= read -r line; do
    case "$line" in \#*|"") continue ;; esac
    k="${line%%=*}"; v="${line#*=}"
    case "$k" in
      NAS_SSH|NAS_SSH_PORT|NAS_APP_DIR|APP_LAN_IP*|PROXY_LAN_IP|COUCHDB_LAN_IP)
        $any || { printf -- '\n> [!abstract] Deployed as\n'; any=true; }
        printf -- '> - `%s` = `%s`\n' "$k" "$v" ;;
    esac
  done < "$envfile"
  $any && printf -- '\n'
  return 0
}

# render <src> <dst-basename> — the note body, on stdout.
#
# A .yml source is wrapped in a fenced block: pasted raw, its leading `#`
# comments render as H1 headings and the note is a mess.
render_note() {
  local src="$1" rel="$2"
  printf -- '---\nsource: %s\n---\n' "$rel"
  printf -- '> [!note] Synced from `%s` by `homelab/vault-sync.sh` — edit the source, not this note.\n' "$rel"
  deploy_facts "${rel%%/*}"
  case "$src" in
    *.yml|*.yaml) printf '```yaml\n'; cat "$src"; printf '\n```\n' ;;
    *)            cat "$src" ;;
  esac
}

changed=0; skipped=0; same=0
while IFS='|' read -r rel dst; do
  [ -n "${rel:-}" ] || continue
  src="${PROJECTS}/${rel}"
  out="${DEST}/${dst}"

  if [ ! -f "$src" ]; then
    if case "$rel" in */deploy-out/*) true;; *) false;; esac; then
      echo "  ~ ${dst}: no rendered compose yet (run that project's ./deploy render)"
    else
      echo "  ✗ ${dst}: missing source ${rel}" >&2
    fi
    skipped=$((skipped + 1)); continue
  fi

  tmp="$(mktemp -t vault-sync)"
  render_note "$src" "$rel" > "$tmp"

  if [ -f "$out" ] && cmp -s "$tmp" "$out"; then
    same=$((same + 1)); rm -f "$tmp"; continue
  fi

  # Refuse to clobber a note that is not ours. A managed note always carries the
  # source: line; anything else is hand-written and stays that way.
  if [ -f "$out" ] && ! head -3 "$out" | grep -q '^source: '; then
    if ! $ADOPT; then
      echo "  ! ${dst}: exists and is NOT marked as synced — leaving it alone."
      echo "    It is probably an older hand-placed copy; \`--adopt\` takes it over"
      echo "    (backing the original up outside the vault first)."
      rm -f "$tmp"; skipped=$((skipped + 1)); continue
    fi
    # One-time takeover. Back up OUTSIDE the vault: a .bak inside it would show
    # up in Obsidian as another note and replicate to every device.
    mkdir -p "$BACKUP"
    cp "$out" "${BACKUP}/$(echo "$dst" | tr / _)"
    echo "  ↳ adopted (original kept in ${BACKUP})"
  fi

  if $CHECK; then
    echo "  → ${dst}: would update ($([ -f "$out" ] && echo changed || echo new))"
  else
    mkdir -p "$(dirname "$out")"
    mv "$tmp" "$out"
    echo "  ✓ ${dst}"
  fi
  rm -f "$tmp" 2>/dev/null || true
  changed=$((changed + 1))
done <<< "$MANIFEST"

echo
$CHECK && echo "${changed} would change, ${same} already current, ${skipped} skipped." \
       || echo "${changed} written, ${same} already current, ${skipped} skipped."
