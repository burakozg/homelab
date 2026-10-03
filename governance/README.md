# governance

Status, architecture and models as three static pages, served from the NAS behind
the central login.

| page | source | refreshed |
|---|---|---|
| **Status** `/` | the NAS's `live.json` + the Mac's snapshots, rendered on the NAS (`../nas-jobs`) | every 30 min, by `homelab-jobs` |
| **Architecture** `/architecture` | `content/architecture.{html,css}`, by hand | when you edit it and `./deploy publish` |
| **Models** `/models` | each repo's `config.yaml` + `content/models-hardware.html` | daily, by the Mac's slow run |

## How it fits together

```
NAS: homelab-jobs ──live.json + Mac snapshots──▶ build.py --status-only ──▶ NAS: $NAS_JOBS_DIR/site/index.html
Mac: status.sh ──fast/slow.json──▶ nas-jobs/deploy push ─ssh─▶ NAS: homelab-jobs data/
Mac: governance/deploy publish ──build.py──▶ architecture.html, models.html ─ssh tar─▶ NAS: $NAS_APP_DIR/site/
Browser ─▶ Traefik ─forwardAuth─▶ homelab-auth ─▶ `governance` (nginx, :8080)
                                                    / from the jobs site/, the rest from site/ (read-only)
```

- **No restart on refresh.** nginx serves a bind-mounted directory; `publish`
  replaces the files in place, and `homelab-jobs` rewrites `index.html` itself.
- **Gated as a whole host.** Router `governance-web` carries
  `homelabAuth-forward`; there is no `/healthz` carve-out, because the status
  collector does not probe this site. The container has no published port and sits
  only on `homelab-internal`, so Traefik is the only way in.
- **Staleness is visible.** Ages are recomputed in the browser; a red banner
  appears when the snapshot is older than the collector's own threshold.
- **The Models page cannot silently drift.** Model ids come from the config files
  at build time; any id the hardware table does not mention is flagged. It reports
  the *configured* value — an env var on the NAS can still override it, which the
  Status page, not this one, would have to catch.

## Setup (once)

```sh
cp deploy.env.example .deploy.env     # fill in the real host, NAS paths, Traefik IP
./deploy                              # build image, ship it and the pages, start it
./deploy traefik                      # prints the router + service to add to
                                      # Traefik's dynamic-config.yml on the NAS
./deploy check                        # container up, and the login gate in front
```

Traefik's `dynamic-config.yml` is untracked and shared by every app: back it up
(`cp -p dynamic-config.yml dynamic-config.yml.bak-<timestamp>`) and write it back
with `cat >` rather than `mv`, which would swap the inode out from under a bind
mount. `*.<domain>` already resolves to Traefik through dnsmasq's wildcard, so a
new host needs no DNS change.

`./deploy check` asks Traefik from the Mac the way a browser arrives and fails
unless the unauthenticated request is bounced to the login — the container
answering `/healthz` internally proves nothing about the front door.

## Verbs

`./deploy help` lists them; `publish` is the one the collector runs. `./build.py`
alone builds into `deploy-out/site/` for a look without touching the NAS, and
`python3 tests.py` covers the build.
