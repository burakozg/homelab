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
| **Clippings → Topics** | Reads saved web clippings in the Obsidian vault, works out what each is about, and links them into the shared topic pages. | [clippings-topics](https://github.com/burakozg/clippings-topics) |
| **video-digest** | Takes a video URL, gets the best available transcript, and writes a summarised, timestamped Obsidian note. | [video-digest](https://github.com/burakozg/video-digest) |

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
  **Except for live SQLite databases** — see `nas_sqlite_get` below; copying
  one that way returns a plausible file that is silently missing data.
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

## Deploying: one contract, four repos

Every project deploys with `./deploy` at its repo root. No `deploy.sh`, nothing
nested under `qnap/`, and — the point of the exercise — no per-repo flag to
remember. A bare `./deploy` does the whole correct thing for that project.

| verb | what it does |
|---|---|
| *(none)* | same as `up` |
| `ship` | build the artefact and put it on the NAS. **Never touches containers.** |
| `apply` | make the NAS run what was shipped (see `LIFECYCLE` below) |
| `up` | `ship` then `apply` |
| `render` | write `deploy-out/…` with real values filled in, for pasting into Container Station |
| `check` | health-probe and report |
| `help` | usage |

Projects keep extra verbs of their own where they earn one (`mac`, `data-pull`,
`sync-check`, `proxy`, `backup`, `relay`).

### `LIFECYCLE`, declared per project

All four are now **`LIFECYCLE=compose`**: `apply` ships the compose file and runs
`docker compose up -d` over ssh. Every deploy is one command, on every project.

The setting remains because it earned its keep during the migration. Two of the
four were `LIFECYCLE=station` — their containers belonged to Container Station
Applications, so `apply` rendered YAML to the clipboard and told you to press
Recreate, and deliberately never ran `docker run`/`stop`/`rm` itself. Declaring
that per project is what let a bare `./deploy` be correct everywhere while the two
models coexisted, instead of asking you to remember a flag. Both have since moved
to `compose`; `station` is kept documented for anything added later that genuinely
needs the UI to own its lifecycle.

### Container Station: what it's still for

An "Application" is a plain compose project too — same `docker compose` v2.29.1
— differing only in that Container Station keeps its **own copy** of the YAML
under `.qpkg/container-station/data/application/<name>/`, and "Recreate" re-reads
that copy rather than the repo's file. That single fact is the source of the
re-paste footgun documented above.

Measured on this NAS, being an Application buys nothing the compose projects
lack:

| | Application | compose over ssh |
|---|---|---|
| Mechanism | compose 2.29.1 | compose 2.29.1 — identical |
| Source of truth | Container Station's private copy | the file `./deploy` ships |
| Deploy step | render → paste → click | automatic |
| Survives a reboot | `restart: unless-stopped` | `restart: unless-stopped` — identical |
| Resource limits | rejected in YAML; set in its UI panel | version-controlled in the tracked file |

Boot survival is the usual argument for Applications and it doesn't hold: every
container here carries `restart: unless-stopped`, which the docker daemon honours
whoever started it. Meanwhile the resource panel Container Station forces you
into held nothing but zeros for the digests, while Podcast Digest caps itself at
3G *in its tracked compose file* precisely because it isn't an Application.

So Container Station is kept for what only it can do — it owns the
`qnet-static-eth1-dc7e3a` macvlan every project joins as `external: true`, runs
the docker daemon, and lists every container under **Containers** for logs and
start/stop. The Application wrapper is gone from all four.

A project that compose takes over gets demoted in the Applications list to a
*discovered* entry (marked with an ⓘ), offering only Start/Restart/Stop/Inspect —
no Recreate, and no Remove. That is the desired end state, not a problem to fix:
the Recreate button was the footgun, and it is what disappears. Container Station's
stored copy of the YAML is left behind under
`.qpkg/container-station/data/application/<name>/` and is inert.

### Pin MAC addresses on the macvlan

Anything with a qnet address must pin its MAC, per network (`networks.<net>.mac_address`,
not on the service — a service-level pin lands on only one interface and picks the
wrong one on a multi-network service). `nas_mac_for_ip` derives it, and each
project's `./deploy` fills it in at deploy time so the value — which encodes the
real LAN address — stays out of git.

Docker randomises the MAC on every `create` for macvlan, unlike bridge networks
where it derives one from the IP. The router then keeps answering for the old MAC,
and the container comes back healthy, correctly routed, able to reach the internet,
and **unreachable inbound for minutes** — with its healthcheck none the wiser,
because that probes localhost from inside. It self-heals once the container sends
anything outbound, which makes it maddening to diagnose. Pin the MAC and a recreate
is invisible instead.

This is what retired the old `--skip-run` / `--no-up` split. Those flags existed
because a script *could* do the wrong thing to a Container Station-managed
container — reverting it to port mappings and losing its static IP, silently.
Deleting that code path was worth more than documenting the flag. The one
remaining escape hatch, `--no-apply` (stop after `ship`), is spelled the same in
all four repos and is now rarely needed.

### Variable names

Set in each repo's git-ignored `.deploy.env` (see its `deploy.env.example`):

| var | meaning | default |
|---|---|---|
| `NAS_SSH` | ssh destination, `user@host` — **always** the NAS host account | `deploy@nas.local` |
| `NAS_SSH_PORT` | | `22` |
| `NAS_APP_DIR` | this project's directory on the NAS | per project |
| `NAS_DOCKER_BIN` | Container Station's docker | `/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker` |
| `NAS_PLATFORM` | cross-build target | `linux/amd64` |
| `APP_LAN_IP` | the **container's** address on the qnet macvlan (suffixed when a project has several: `APP_LAN_IP_NEWS`) | placeholder |

**`NAS_HOST` is retired and rejected with an error.** It used to mean the ssh
destination in two repos and a container's macvlan IP in a third, so a value
copied between repos either broke the deploy or aimed ssh at a container. Since
`.deploy.env` is git-ignored, nothing in any repo could migrate it automatically
— the error is what turns a forgotten rename into a loud failure.

### `deploy.lib.sh`

The ssh/transfer plumbing all four had grown separately — and disagreed on —
lives in `deploy.lib.sh` here, **vendored** as a byte-identical copy into each
project. Copied, not depended on: each repo keeps standing alone when published,
which is the property this directory's opening paragraph is about. The cost is
that a fix in one copy is invisible to the others, so `./vendor-check.sh` reports
drift and `./vendor-check.sh --sync` re-vendors from the canonical copy here.

It carries the best version of each piece rather than a fresh rewrite: the
key-auth probe that stops a non-interactive deploy hanging on a password prompt,
the byte-count check after every file push, the non-empty check on every pull
(its absence is what once let a deploy delete admin-panel edits it had failed to
fetch), the manifest prune that makes a source push a mirror rather than an
overlay, and the `docker image inspect` after every `docker load`, because
`docker load` exits 0 after failing mid-stream.

#### `nas_sqlite_get` — the one case `ssh cat` gets wrong

`nas_sqlite_get <container> <db-path-in-container> <local>`. Use it for any
SQLite database a running container is writing to; never `nas_get`.

Every database here runs in **WAL** mode, where committed transactions live in
a `-wal` sidecar until a checkpoint folds them into the `.db`. Copying the
`.db` alone therefore yields a file that opens fine and passes
`PRAGMA integrity_check` while missing recent writes — sometimes all of them.
Demonstrated on a database with three committed rows:

```
live db: .db=4096B  -wal=20632B  (3 rows committed)

naive  ssh cat .db    : integrity=ok   QUERY FAILED — no such table: notes
safe   nas_sqlite_get : integrity=ok   rows=3
```

Both report `integrity=ok`. Nothing warns you the copy is empty — and whether
it is depends on when the last checkpoint happened, so it fails intermittently
and looks like something else. `nas_get`'s non-empty check cannot catch this:
the file is 4 KB of valid header, not zero bytes.

The snapshot runs **inside the container** because it cannot run anywhere else:
this NAS has neither `sqlite3` nor any `python` on the host. The application
images have python, and SQLite's online backup API takes a consistent snapshot
while the application keeps writing — no stop, no downtime. Verified against
vault-ask's live 62 MB index: integrity ok, full row counts, service
uninterrupted.

Note the redirect-inside-`sh -c` rule applies here as everywhere:
`docker exec <c> wc -c < /path` resolves the redirect in the *host* shell and
reports "No such file" for a file that is present in the container. That bug
was written, and caught, while implementing this function.

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

## Writing into the vault

**Two vaults now, split 2026-09-02** — same CouchDB server (`taster-couchdb`),
two databases. `taster`'s content was never related to the other four's, and
splitting it out cleared the way for a second, non-security use of the same
sync mechanism (family-calendar's recipes and events). See "The vault split"
below for why and how; everything in this section applies to either vault, a
new writer just needs to know which database it's joining.

**Security vault** (db `tastings`) — four applications write notes into it:
`podcast-digest`, `security-digest`, `clippings-topics` and `video-digest` —
plus `vault-sync.sh` below and the person whose vault it is. They share one
file whenever a note has to be one node in the graph: a topic page for
"Anthropic" that several of them describe. Splitting it would put two
`anthropic.md` in the vault and draw a graph that quietly lies about how much
connects.

**Hobby vault** (db `hobby`) — `taster` (tasting notes, `Tastings/`) and
`family_calendar` (recipes, events, birthdays — `Recipes/`, `Calendar/`), both
whole-file only. Nothing shared between them, so neither needs an owner tag or
frontmatter prefix.

The rules are in `~/.claude/skills/obsidian-vault-writer`, and they are not
optional — a writer that assumes it is alone destroys someone else's work
silently, and the damage looks like Obsidian losing notes rather than a bug.
In short: each writer owns a region marked with an HTML comment
(`<!-- begin:podcast-digest -->`) and a frontmatter prefix (`podcasts_`), merges
against **the vault** rather than its own copy, and never touches an unprefixed
key like `tags`. `podcast_agent/notes.py` is the reference implementation; the
other writers carry byte-vendored copies differing only in owner and prefix.

Owner tags in use in the security vault, so a new writer does not collide:

| owner tag | frontmatter prefix | writes |
|---|---|---|
| `podcast-digest` | `podcasts_` | `11 podcasts/`, `99 topics/` |
| `security-digest` | `security_` | `99 topics/` and its own digests |
| `clippings` | `clip_` | `99 topics/` only |
| `video-digest` | `video_` | `13 video-summaries/`, `14 video-transcripts/` (whole-file), `99 topics/` |
| *(vault-sync.sh)* | `source:` | `30 projects/`, whole-file |

The hobby vault's two writers (`taster`, `family_calendar`) don't appear here —
whole-file ownership on disjoint folders needs no owner tag.

`clippings-topics` also runs the **duplicate reaper** for all of them. The vault
is replicated twice over — iCloud syncs the folder while LiveSync syncs the same
notes through CouchDB — so when a server-side writer *creates* a note, a client
can find the path already taken and Obsidian appends `" 2"`. 54 notes had picked
one up by 2026-08-26. No *writer* can prevent it (the copy is made client-side,
after the write has already succeeded), so it is cleaned up afterwards, and only
when the copy is **byte-for-byte** its base — `10 raw/` legitimately holds `" 1"`
and `" 2"` files from the Web Clipper. Run it alone with:

```sh
cd ../clippings-topics && python -m clippings_topics --reap-only --dry-run
```

It **is** preventable at the device level, though the fix lives outside any
app: the race needs two things materialising the same new file at once, so
with exactly one device running the LiveSync client against this vault there
is nothing left to race — iCloud just propagates what that device wrote out to
everywhere else.

**A "just uninstall it on N-1 devices" attempt at this failed in practice
(2026-08-30) and is worth recording so it isn't retried.** Obsidian plugins
are not an OS/app-level install — they're files under the vault's own
`.obsidian/plugins/` folder. If iCloud syncs the whole vault folder (it does,
by default), `.obsidian` syncs too, so uninstalling the plugin on one device
deletes those files from the *shared* folder every device sees — it
propagated the removal to every device, including the intended hub. There is
no supported way to have the plugin present on some devices and absent on
others while iCloud syncs `.obsidian` for all of them; plugin presence is
vault content, not device state.

This is also confirmed by the plugin's own documentation, which is more
direct than this doc used to give it credit for: **"Do not enable this
plug-in alongside another synchronisation solution (including iCloud and
Obsidian Sync)."** Combining them was never a supported configuration — the
duplicate-note race above and the whole-vault plugin deletion are both
predictable consequences of doing it anyway, not two unrelated bugs.

**Corrected plan: drop iCloud for this vault entirely; LiveSync is the only
sync mechanism, on every device.** This is what the plugin is actually built
for (it already covers phone + laptop for the other four apps, with no
iCloud involved there), and it removes the race at the root rather than
containing it — with exactly one live sync channel, nothing races. **Done as
of 2026-09-01** — the Mac and both phones/iPad reconnected via LiveSync
directly, each bootstrapped through a brand-new empty local vault rather than
trusting the setup wizard's fetch/merge logic against an already-populated
one (two open upstream issues, vrtmrz/obsidian-livesync #800 and #607, both
involve exactly that path silently losing or corrupting content). The reaper
stays on regardless, as defense-in-depth for the day this gets violated
again.

## The vault split

`taster`'s content (`Tastings/`) never related to the other four apps' —
confirmed by an exhaustive grep before splitting it out, zero cross-links
either direction — so on 2026-09-02 it moved to its own database (`hobby`)
on the same `taster-couchdb` server, which also became the home for
`family_calendar`'s new integration (recipes, meal plans, events, birthdays,
kept live-synced, not a one-off export). A second database on the existing
server was the low-friction move: no new macvlan IP/MAC to pin, no new
CORS/LiveSync `.ini` (that config is server-wide, so it already covers
`hobby` for free), and `backup-vault.sh` needed only a second scheduled
invocation, not a code change.

Migration: taster's existing `Tastings/` data was copied into `hobby`
(byte-verified, 68/68 notes identical), taster's live deployment was
repointed at it (`COUCHDB_DB=hobby`, a new least-privilege member account —
see "Config that must never be tracked" — rather than the admin account it
was using before), and only once that was verified did `Tastings/` get
soft-deleted out of `tastings` — which is what actually made it disappear
from the security vault on all four devices. The old copy was never touched
until the new one was confirmed working end to end, same discipline as the
iCloud migration above.

`family_calendar`'s writer (`backend/vault_writer.py`) is a new,
from-scratch vault integration — the app had no prior Obsidian/CouchDB code
at all — vendoring the same chunk+entry LiveSync client every other repo
carries a copy of. It rebuilds `Recipes/<id>.md` and
`Calendar/Events.md`/`Calendar/Birthdays.md` whole on every cycle (same
"rebuilt whole, never appended to" convention `clippings-topics` uses),
triggered from the app's existing mutation points rather than a new storage
layer.

## Backing up the vault

LiveSync copies on any device — Mac, phone, iPad — are sync artefacts, not
backups (`taster/tasting-log-design.md` §7). The vault's CouchDB — now two
databases, `tastings` (security vault, four apps) and `hobby` (taster +
family-calendar) on the same server, see "The vault split" above — is the
one thing here that actually needs a backup story, and until 2026-08-30 it
didn't have a real one — only an unverified suggestion to fold it into a
generic QNAP backup job. `backup-vault.sh` replaces that with a tested
mechanism, modelled on `podcast-digest/scripts/backup.sh`:

```sh
cd homelab && ./backup-vault.sh                 # tastings -> $VAULT_BACKUP_DIR
cd homelab && VAULT_DB=hobby ./backup-vault.sh   # hobby -> the same dir
```

`VAULT_DB` defaults to `tastings` from `.env`, but a pre-exported `VAULT_DB`
(as the hobby LaunchAgent below sets) wins over `.env`'s value — both
databases share the same server and admin credentials, so only the database
name needs to differ between the two scheduled runs.

Deliberately run from the Mac, not the NAS: the vault's CouchDB is
LAN-reachable directly (unlike podcast-digest's own state DB, which is
loopback-only on purpose), so the dump is created off the NAS from the start
— a single NAS disk failure can't take out the live data and its backup
together. `_all_docs?include_docs=true&attachments=true`, gzip, verified
non-empty and JSON-valid before the file is published, 14 kept by default.
Output lands outside the iCloud-synced vault folder (`~/Backups/vault-couchdb`
by default) — it's an archive, not a live synced folder, and mixing the two
would risk exactly the kind of sync confusion the section above exists to
avoid.

Scheduled nightly via two LaunchAgents rather than NAS cron, for the same
off-NAS reasoning — 15 minutes apart so the two runs don't hit CouchDB at
once:

| db | LaunchAgent | time |
|---|---|---|
| `tastings` | `com.homelab.vault-backup.plist` | 03:30 |
| `hobby` | `com.homelab.vault-backup-hobby.plist` | 03:45 |

`launchctl list | grep homelab` to check both are loaded, `launchctl start
com.homelab.vault-backup` (or `-hobby`) to run either on demand. Only fires
while the Mac is awake; a Mac that's reliably asleep overnight needs either a
different schedule or `pmset` wake configuration, not covered here yet.

## The Obsidian vault

`vault-sync.sh` pushes these projects' main docs into the Obsidian vault's
`30 projects/` folder, so the copy you read on your phone is the current one
rather than whatever was pasted months ago.

```sh
./vault-sync.sh --check    # what would change
./vault-sync.sh            # write it
./vault-sync.sh --adopt    # one-time: take over older hand-placed copies
```

Three things it is careful about:

- **It only writes files in its own manifest.** Hand-written notes beside them —
  the LAN address index, `daily-digests/Deploy` — are never touched. A managed
  note is identifiable by the `source:` line in its frontmatter, and the script
  refuses to overwrite a file that lacks one unless you pass `--adopt` (which
  backs the original up outside the vault first).
- **Unchanged docs produce byte-identical files**, so a run writes nothing when
  nothing changed. The vault replicates over both iCloud and Obsidian LiveSync;
  a timestamp in the header would mean re-replicating every note on every run.
- **Compose files come from `deploy-out/`, not the tracked template.** Those are
  rendered with the real addresses and MACs, which is what makes them useful as
  an ops reference. Prose docs keep their placeholders — substituting into prose
  would risk a doc that is subtly wrong rather than obviously generic — so each
  note instead gets the project's real values prepended from its `.deploy.env`.

The vault is a reference copy, never a source: edit the doc in its repo and rerun.

## License

MIT — see [LICENSE](LICENSE).
