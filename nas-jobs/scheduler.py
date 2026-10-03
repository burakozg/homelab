#!/usr/bin/env python3
"""The homelab-jobs container's only process: run the homelab's scheduled jobs.

    scheduler.py             run forever (the container's command)
    scheduler.py --run NAME  run one job now, in the foreground, and exit

Jobs, all stdlib-and-shell, all things that used to be Mac LaunchAgents and
therefore did not happen while the laptop was shut:

    backup-the_brain  03:30  backup-vault.sh
    backup-hobby      03:45  backup-vault.sh
    vault-doctor      04:00  fix what is mechanically safe, then record what remains
    live              every 30 min  probe the apps, then rebuild the status page

There is no cron on the NAS, hence this. State is kept in /data/jobs.json so a
restart does not repeat a job that already ran today, and a restart that follows
a missed slot (the container was down at 03:30) catches up straight away instead
of waiting a day.

Each job runs in its own thread with its own output prefix, so a slow backup
never delays the status refresh, and a job still running when its next slot comes
round is skipped rather than stacked.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

APP = Path(os.environ.get("JOBS_APP", Path(__file__).resolve().parent.parent))
DATA = Path(os.environ.get("HOMELAB_STATUS_DIR", "/data"))
SITE = Path(os.environ.get("JOBS_SITE", "/site"))
STATE = DATA / "jobs.json"
HEARTBEAT = Path(os.environ.get("JOBS_HEARTBEAT", "/tmp/heartbeat"))
TICK = 20

_print_lock = threading.Lock()
_state_lock = threading.Lock()


def say(name: str, text: str) -> None:
    with _print_lock:
        for line in str(text).rstrip("\n").splitlines() or [""]:
            print(f"[{name}] {line}", flush=True)


def run(name: str, argv: list[str], timeout: int, env: dict[str, str] | None = None) -> int:
    """Run a command, echoing its output under `name`. Never raises."""
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            env={**os.environ, **(env or {})},
            start_new_session=True,
        )
    except OSError as exc:
        say(name, f"cannot start {argv[0]}: {exc}")
        return 127
    timer = threading.Timer(timeout, lambda: _kill(proc))
    timer.start()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            say(name, line)
        rc = proc.wait()
    finally:
        timer.cancel()
    if rc < 0:
        say(name, f"killed after {timeout}s (signal {-rc})")
    return rc


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), 9)
    except (ProcessLookupError, PermissionError):
        pass


# ── the jobs ─────────────────────────────────────────────────────────────────


def backup(db: str) -> Callable[[], bool]:
    def go() -> bool:
        return run(f"backup-{db}", ["bash", str(APP / "backup-vault.sh")], 40 * 60, {"VAULT_DB": db}) == 0

    return go


def vault_doctor() -> bool:
    """Repair, then rescan read-only and record what is left.

    The second pass is what the status page shows: the first one's output is a
    log of what it changed, not of what is still wrong. Scanning parses the whole
    vault, so it happens here once a night rather than on every status refresh.
    """
    script = str(APP / "vault-doctor.py")
    ok = run("vault-doctor", ["python3", script], 30 * 60) == 0
    out = subprocess.run(
        ["python3", script, "--json"], capture_output=True, text=True, timeout=30 * 60, check=False
    )
    try:
        payload = json.loads(out.stdout)
    except ValueError:
        say("vault-doctor", f"--json produced no usable output: {(out.stdout + out.stderr)[:200]}")
        return False
    health = {"checked_at": datetime.now(UTC).isoformat(), **payload}
    tmp = DATA / "vault-health.json.tmp"
    tmp.write_text(json.dumps(health, indent=2), encoding="utf-8")
    tmp.replace(DATA / "vault-health.json")
    return ok


def live() -> bool:
    ok = run("live", ["python3", str(APP / "status" / "collect.py"), "--live"], 10 * 60) == 0
    # Rebuild the page even if a probe misbehaved: the page's own staleness
    # banner is the backstop, and a page that stops updating hides the failure.
    build = run(
        "live",
        ["python3", str(APP / "governance" / "build.py"), "--status-only", "--out", str(SITE)],
        2 * 60,
    )
    return ok and build == 0


@dataclass(frozen=True)
class Job:
    name: str
    work: Callable[[], bool]
    #: Local wall-clock (hour, minute) for a daily job, or None.
    at: tuple[int, int] | None = None
    every: timedelta | None = None


JOBS = (
    Job("backup-the_brain", backup("the_brain"), at=(3, 30)),
    Job("backup-hobby", backup("hobby"), at=(3, 45)),
    Job("vault-doctor", vault_doctor, at=(4, 0)),
    Job("live", live, every=timedelta(minutes=30)),
)

# ── scheduling ───────────────────────────────────────────────────────────────


def load_state() -> dict[str, dict]:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(name: str, entry: dict) -> None:
    with _state_lock:
        state = load_state()
        state[name] = entry
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(STATE)


def due(job: Job, now: datetime, last_start: datetime | None) -> bool:
    """Is `job` owed a run? `now` and `last_start` are timezone-aware."""
    if job.every is not None:
        return last_start is None or now - last_start >= job.every
    assert job.at is not None
    slot = now.replace(hour=job.at[0], minute=job.at[1], second=0, microsecond=0)
    return now >= slot and (last_start is None or last_start < slot)


def execute(job: Job) -> None:
    started = datetime.now().astimezone()
    say(job.name, "start")
    t0 = time.monotonic()
    try:
        ok = job.work()
    except Exception as exc:  # noqa: BLE001 - one job's failure must not end the scheduler
        say(job.name, f"crashed: {type(exc).__name__}: {exc}")
        ok = False
    seconds = round(time.monotonic() - t0)
    say(job.name, f"{'ok' if ok else 'FAILED'} in {seconds}s")
    save_state(job.name, {"started": started.isoformat(), "ok": ok, "seconds": seconds})


def last_start(name: str) -> datetime | None:
    raw = load_state().get(name, {}).get("started")
    try:
        return datetime.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    SITE.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) == 3 and sys.argv[1] == "--run":
        job = next((j for j in JOBS if j.name == sys.argv[2]), None)
        if job is None:
            print(f"no job {sys.argv[2]!r}; have: {', '.join(j.name for j in JOBS)}", file=sys.stderr)
            return 2
        execute(job)
        return 0 if load_state()[job.name]["ok"] else 1

    say("scheduler", f"up; TZ={os.environ.get('TZ', '(unset)')}; jobs: {', '.join(j.name for j in JOBS)}")
    running: set[str] = set()
    guard = threading.Lock()

    def launch(job: Job) -> None:
        try:
            execute(job)
        finally:
            with guard:
                running.discard(job.name)

    while True:
        HEARTBEAT.write_text(str(int(time.time())))
        now = datetime.now().astimezone()
        for job in JOBS:
            with guard:
                if job.name in running or not due(job, now, last_start(job.name)):
                    continue
                running.add(job.name)
            threading.Thread(target=launch, args=(job,), daemon=True).start()
        time.sleep(TICK)


if __name__ == "__main__":
    raise SystemExit(main())
