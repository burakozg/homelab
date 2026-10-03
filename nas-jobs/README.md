# nas-jobs

The homelab's scheduled jobs, run by one small container on the NAS
(`homelab-jobs`) instead of Mac LaunchAgents — so they happen while the laptop is
shut.

| job | when | does |
|---|---|---|
| `backup-the_brain` | 03:30 | `../backup-vault.sh` for the security vault |
| `backup-hobby` | 03:45 | the same for the taster/family-calendar vault |
| `vault-doctor` | 04:00 | `../vault-doctor.py`: fix what is mechanically safe, record the rest in `vault-health.json` |
| `live` | every 30 min | probe the apps, write `live.json`, rebuild the Status page |

There is no cron on the NAS, hence `scheduler.py`. It keeps `jobs.json` so a
restart does not repeat a job that ran today, and a restart after a missed slot
catches up immediately. A job still running when its next slot arrives is skipped,
not stacked. First start therefore runs everything once.

## What stays on the Mac

Tests, git state, outdated packages, container state and the deployed-commit
comparison (`../status.sh`). The container has **no `docker.sock`** on purpose.
The Mac pushes `fast.json` / `slow.json` here after each run (`./deploy push`).

## Use

```sh
cp deploy.env.example .deploy.env   # real host, NAS_APP_DIR, JOBS_BACKUP_DIR
./deploy             # build, ship, start
./deploy check       # healthy? live.json fresh? newest backup per db?
./deploy run live    # run one job now (also vault-doctor, backup-the_brain, backup-hobby)
./deploy logs
./deploy push        # send the Mac's snapshots (status.sh does this)
```

`./deploy render` writes `deploy-out/` (compose, `targets.json`, and `jobs.env` —
credentials, mode 600, git-ignored). `targets.json` is the app registry resolved
for the NAS: apps are reached by container name on `homelab-internal`, or by their
LAN address, rather than through Traefik from outside.

## Things that bit

- **Backups go to a different volume** from the live CouchDB (`JOBS_BACKUP_DIR`
  on `CACHEDEV3_DATA`). Different filesystem; physical disk separation is not
  verified.
- **Memory.** The NAS is tight; the container is capped at 384 MB. The doctor
  pages through `_all_docs` and the backup verifies by streaming for that reason.
- **Health is the heartbeat file**, refreshed by the scheduler loop; "healthy"
  means the loop is alive, not that every job succeeded — `./deploy check` and the
  Status page cover that.
