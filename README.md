# Homelab

Infrastructure notes and a map of the self-hosted services running on one
QNAP NAS (Container Station) at home. Each service below is independently
developed and has its own repo — this one exists because four unrelated
projects converged on the same handful of solutions to the same handful of
problems, and that's worth writing down once instead of four times.

Nothing here is a deploy target itself: there's no shared code, no
docker-compose file, no CI. It's a reference doc plus a directory.

## Projects

| Project | What it does | Repo |
|---|---|---|
| **Family Calendar** | Self-hosted family calendar synced to a Pimoroni Inky Frame e-ink display, with AI-assisted weekly meal planning. | [family_calendar](https://github.com/burakozg/family_calendar) |
| **Podcast Digest Agent** | Monitors podcasts, decides which episodes matter, transcribes and summarises them into a weekly Markdown digest synced to Obsidian. | [podcast-digest](https://github.com/burakozg/podcast-digest) |
| **Security Digest** | Fetches security news, summarises and categorises it with an LLM, and delivers curated digests by email on a schedule. | [security-digest](https://github.com/burakozg/security-digest) |
| **Tasting Log** | Photo/chat capture of whisky, coffee, and other tastings via Claude vision, with lookup and Obsidian sync. | [taster](https://github.com/burakozg/taster) |

(Links assume each repo is published under this name — update if you named
any of them differently.)

## The platform

All four run on a QNAP NAS via **Container Station**, either as plain
`docker compose` stacks (SSH-deployed) or as Container Station "Application"
definitions (YAML pasted into the UI). A few things about this specific NAS
turned out to matter enough that every project's deploy tooling ended up
working around them the same way:

- **`scp` and `sftp` don't work against this host's sshd** — no SFTP
  subsystem, so both fail with `subsystem request failed on channel 0` /
  exit 255. Every project moves files with plain `ssh host "cat > path" <
  local-file` instead, which works everywhere because it's just command
  execution with stdin piped through, not a file-transfer protocol.
- **`docker` isn't on `PATH` for non-interactive SSH sessions** — Container
  Station only wires it in for interactive logins. Scripts address it by
  full path (`/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker`)
  rather than relying on resolution.
- **Container Station's stored Application YAML is a separate copy from the
  file in your repo.** "Recreate" re-reads what's stored in Container
  Station, not your file — a mount added or changed in the repo has zero
  effect until you re-paste the YAML.
- Docker silently creates a **directory** at any bind-mount host path that
  doesn't exist yet. A deploy script that pushes config files before the
  container's first run avoids turning a typo'd path into a permanent,
  confusing empty directory.

## Networking: one shared macvlan, one static IP each

Every project that needs a stable LAN address (rather than a NAT'd port
mapping) joins the same Container Station macvlan network and claims its own
static IP on it — the standard way to give several unrelated Docker stacks
predictable addresses on one physical NIC without them colliding or needing
`ports:` entries. Two things about *this* NAS's macvlan are non-obvious
enough that they cost real debugging time across more than one project:

- **The NAS host itself often cannot reach its own macvlan children** — a
  property of how macvlan works, not a misconfiguration. Don't debug "NAS
  can't reach the container" as a networking fault; check from another
  machine on the LAN instead.
- **No embedded Docker DNS on this network**, and container-to-container
  traffic between two macvlan addresses has been observed to fail outright
  even by IP. Anything that needs reliable service discovery between
  containers on the same host uses a private bridge network instead — the
  macvlan address is for the LAN's benefit, not for containers talking to
  each other.

## Keeping the NAS off the public internet

None of these services port-forward anything. Three different patterns
cover "reachable when I'm not home" without ever exposing the NAS directly:

1. **LAN-only, admin-token gated.** No public exposure at all — the service
   only answers on the home network, and write/admin actions require a
   bearer token even there. Simplest option when remote access isn't a
   requirement (Security Digest, Podcast Digest Agent).
2. **Outbound-only via a small cloud relay.** A tiny relay service (Fly.io)
   is the only internet-facing piece; the NAS-side worker polls it
   *outbound* for jobs and posts results back the same way, so nothing ever
   listens for inbound connections at home (Tasting Log).
3. **Real HTTPS on a private IP.** A reverse proxy on the NAS gets a genuine
   Let's Encrypt certificate via a DNS-01 challenge (proves domain
   ownership through a DNS TXT record, never an inbound connection), for a
   public DNS name that resolves to a private LAN address. Lets a
   cloud-hosted PWA embed the home app in an iframe — browsers require
   HTTPS for that — without opening a port (Family Calendar).

## Config that must never be tracked

Every project's deploy scripts and Docker Compose files read
per-deployment values (NAS host, static IPs, folder paths) as
`${VAR:-placeholder}`, sourced from a git-ignored `.deploy.env` next to a
tracked `deploy.env.example` template — so the real values exist in exactly
one place per project, are never in git history, and never need
re-exporting by hand. The same idea extends to app config that isn't
naturally an env var (recipient email addresses, branding strings): a
small git-ignored override file merged over the tracked defaults at load
time, rather than editing the tracked config directly.

## License

MIT — see [LICENSE](LICENSE).
