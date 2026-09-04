"""Gather the homelab's state into one JSON snapshot.

Two tiers, because they cost different amounts:

* ``--fast`` (seconds) — health, containers, deploy age, drift, backups, queues.
* ``--slow`` (minutes) — test suites and outdated packages.

A slow run *merges* into the existing snapshot instead of replacing it, so the
cached test results survive the next fast run and vice versa. Every section
carries its own ``collected_at`` for the same reason: a page that cannot tell
"healthy" from "not checked since Tuesday" is worse than no page, because it
converts an unknown into a reassurance.

Nothing here raises on a failing probe. One unreachable app degrades its own row
and the run still exits 0 — a collector that aborts on the first problem is
useless on exactly the day it is needed.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import contextlib
import signal
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import registry
from registry import APPS, App

STATUS_DIR = Path(os.environ.get("HOMELAB_STATUS_DIR", Path.home() / ".homelab" / "status"))
#: One file per tier, deliberately. A single snapshot.json meant every run did
#: read-modify-write over the whole document, so a slow run — minutes of test
#: suites — would load, work, and then save its stale copy of `fast` over
#: whatever the 30-minute collector had written meanwhile. The reverse lost the
#: test results just as easily, and did: the first `--slow` run's output was
#: gone within a minute. Separate files cannot race; the reader merges them.
FAST_FILE = STATUS_DIR / "fast.json"
SLOW_FILE = STATUS_DIR / "slow.json"
SNAPSHOT = FAST_FILE  # what --print and the renderer report as the source

HTTP_TIMEOUT = 6
SSH_TIMEOUT = 25
#: Per-suite ceiling. The slowest real suite is podcast-digest at ~90 s, so this
#: is generous — it exists to bound a *hung* suite, not a slow one, and
#: family_calendar's intermittently hangs.
TEST_TIMEOUT = 240
BACKUP_DIR = Path(os.environ.get("VAULT_BACKUP_DIR", Path.home() / "Backups" / "vault-couchdb"))
#: A backup older than this is called out. The job runs daily at 03:30.
BACKUP_STALE_HOURS = 30


def now() -> str:
    return datetime.now(UTC).isoformat()


def _run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 30) -> tuple[int, str]:
    """Run a command, never raise. Returns (returncode, combined output).

    Output goes to a temp file rather than a pipe, and stdin is closed. Both
    matter, and the first is not obvious:

    `capture_output=True` waits for EOF on the pipe, and EOF needs *every*
    process holding that file descriptor to exit — not just the one we started.
    family_calendar's suite leaves a child alive, so it finished in 2.9 s from a
    shell and hung until the 600 s timeout here, reported as a failing suite.
    A file has no such handshake: the child exits, we read what is there.
    """
    try:
        with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as sink:
            # start_new_session puts the child in its own process group, so a
            # timeout can kill the whole tree. Without it, `run` kills only the
            # process it started and any grandchildren keep running — which is
            # how a hung test suite survives its own timeout and then wedges the
            # next run, making a one-off hang look permanent.
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=sink,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.wait(timeout=10)
                rc = 124
            sink.seek(0)
            return rc, sink.read()
    except OSError as exc:
        return 124, f"{type(exc).__name__}: {exc}"


def _get_json(url: str, *, key: str | None = None) -> tuple[Any | None, str | None]:
    """GET a JSON endpoint. Returns (payload, error) — exactly one is set."""
    req = urllib.request.Request(url, headers={"X-API-Key": key} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return json.loads(r.read().decode()), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - any failure is just an unhealthy row
        return None, f"{type(exc).__name__}"


# ── fast signals ─────────────────────────────────────────────────────────────


def probe_health(app: App) -> dict[str, Any]:
    """The app's own health endpoint, kept raw as well as normalised.

    The shapes genuinely differ — podcast-digest reports `{status, couchdb,
    scheduler}`, video-digest `{status, checks{...}, asr_worker}` — so the raw
    body is kept and `ok` is derived. Inventing a common schema would throw away
    the per-app detail that makes a failure diagnosable.
    """
    url = registry.base_url(app)
    if not url:
        return {"probed": False, "reason": "no HTTP surface — container state is the signal"}
    key = registry.app_env(app).get(app.auth_var) if app.auth_var else None
    body, err = _get_json(url + app.health, key=key)
    if err:
        return {"probed": True, "ok": False, "error": err}
    body = body or {}
    status = str(body.get("status", "")).lower()
    checks = body.get("checks") or {}
    bad = [k for k, v in checks.items() if str(v).lower() not in ("ok", "true", "healthy")]
    # Four apps, four vocabularies: "ok", "success", and family-calendar's bare
    # {"ok": true} with no status field at all. Normalise here rather than
    # asking six services to agree on a word.
    healthy = status in ("ok", "healthy", "up", "success") or body.get("ok") is True
    return {
        "probed": True,
        "ok": healthy and not bad,
        "status": status or None,
        "failing_checks": bad,
        "raw": body,
    }


def probe_extra(app: App) -> dict[str, Any]:
    """The richer endpoints: queue depths, job results, spend."""
    url = registry.base_url(app)
    if not url or not app.extra:
        return {}
    key = registry.app_env(app).get(app.auth_var) if app.auth_var else None
    out: dict[str, Any] = {}
    for label, path in app.extra.items():
        body, err = _get_json(url + path, key=key)
        out[label] = body if err is None else {"error": err}
    return out


def containers(host: str, port: str) -> dict[str, Any]:
    """Every container on the NAS in one ssh round trip.

    One call rather than per-app: ssh setup dominates, and the whole table is
    cheaper to fetch than three of its rows.
    """
    docker = "/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker"
    # `|`, not `\t`: docker *inspect*'s Go template emits a literal backslash-t
    # rather than a tab (docker `ps --format` does interpret it, which is the
    # trap). No image name, id or status contains a pipe.
    fmt = (
        "{{.Name}}|{{.State.Status}}|{{.State.StartedAt}}|{{.RestartCount}}"
        "|{{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}}"
        "|{{.Config.Image}}|{{.Image}}"
    )
    rc, out = _run(
        [
            "ssh", "-p", port, "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={HTTP_TIMEOUT}", host,
            f"{docker} inspect --format '{fmt}' $({docker} ps -aq)",
        ],
        timeout=SSH_TIMEOUT,
    )
    if rc != 0:
        return {"error": out.strip()[:200] or f"ssh exit {rc}"}
    found: dict[str, Any] = {}
    for line in out.splitlines():
        parts = line.strip().lstrip("/").split("|")
        if len(parts) < 7:
            continue
        name, state, started, restarts, health, image, image_id = parts[:7]
        found[name.lstrip("/")] = {
            "state": state,
            "started_at": started,
            "restarts": int(restarts) if restarts.isdigit() else None,
            "health": None if health == "-" else health,
            "image": image,
            "image_id": image_id,
        }
    return found


def image_labels(host: str, port: str, tags: list[str]) -> dict[str, dict[str, str]]:
    """The build labels nas_build_labels stamped, per image tag."""
    if not tags:
        return {}
    docker = "/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker"
    script = "; ".join(
        f"printf '%s|' '{t}'; {docker} image inspect '{t}' "
        f"--format '{{{{json .Config.Labels}}}}' 2>/dev/null || echo null"
        for t in tags
    )
    rc, out = _run(
        ["ssh", "-p", port, "-o", "BatchMode=yes", host, script], timeout=SSH_TIMEOUT
    )
    if rc != 0:
        return {}
    labels: dict[str, dict[str, str]] = {}
    for line in out.splitlines():
        tag, _, raw = line.partition("|")
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            labels[tag.strip()] = parsed
    return labels


def repo_state(app: App) -> dict[str, Any]:
    """Branch, uncommitted files, unpushed commits, HEAD."""
    path = registry.repo_path(app)
    if not (path / ".git").exists():
        return {"tracked": False}
    rc, head = _run(["git", "rev-parse", "HEAD"], cwd=path)
    _, branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
    _, dirty = _run(["git", "status", "--porcelain"], cwd=path)
    rc_u, unpushed = _run(["git", "log", "@{u}..HEAD", "--oneline"], cwd=path)
    dirty_files = [ln for ln in dirty.splitlines() if ln.strip()]
    return {
        "tracked": True,
        "head": head.strip() if rc == 0 else None,
        "branch": branch.strip(),
        "dirty": len(dirty_files),
        "unpushed": len([ln for ln in unpushed.splitlines() if ln.strip()]) if rc_u == 0 else None,
    }


def deployed_revision(app: App, container: dict[str, Any], labels: dict[str, Any]) -> dict[str, Any]:
    """How far the running image is from the repo's HEAD.

    `unknown` is a real and expected answer until each app is next deployed —
    the labels only exist on images built after nas_build_labels landed. Saying
    so is the point; guessing from timestamps is what this replaces.
    """
    repo = repo_state(app)
    tag = (container or {}).get("image")
    stamped = (labels.get(tag) or {}).get("org.opencontainers.image.revision")
    if not stamped:
        return {"state": "unknown", "reason": "image predates build labels — redeploy to enable"}
    dirty_build = stamped.endswith("-dirty")
    sha = stamped.removesuffix("-dirty")
    head = repo.get("head")
    if not head:
        return {"state": "unknown", "reason": "repo has no HEAD", "deployed": sha[:8]}
    if sha == head:
        return {
            "state": "dirty" if dirty_build else "in-sync",
            "deployed": sha[:8],
            "built_from_dirty_tree": dirty_build,
        }
    rc, out = _run(["git", "rev-list", "--count", f"{sha}..{head}"], cwd=registry.repo_path(app))
    ahead = int(out.strip()) if rc == 0 and out.strip().isdigit() else None
    return {
        "state": "behind",
        "deployed": sha[:8],
        "head": head[:8],
        "commits_ahead": ahead,
        "built_from_dirty_tree": dirty_build,
    }


def shipped_config_drift(app: App, host: str, port: str) -> dict[str, Any] | None:
    """Whether the NAS's config.yaml still matches the repo's.

    Deploy ships this file, so a mismatch means someone edited it in place — a
    change that survives until the next deploy silently reverts it.
    """
    if not app.shipped_config or not app.app_dir:
        return None
    local = registry.repo_path(app) / app.shipped_config
    if not local.exists():
        return None
    rc, out = _run(
        ["ssh", "-p", port, "-o", "BatchMode=yes", host,
         f"sha256sum '{app.app_dir}/{app.shipped_config}' 2>/dev/null | cut -d' ' -f1"],
        timeout=SSH_TIMEOUT,
    )
    remote = out.strip().splitlines()[0] if rc == 0 and out.strip() else None
    if not remote:
        return {"state": "unknown", "reason": "not readable on the NAS"}
    import hashlib

    mine = hashlib.sha256(local.read_bytes()).hexdigest()
    return {"state": "in-sync" if mine == remote else "drifted"}


def backups() -> dict[str, Any]:
    """Newest dump per database, plus anything left outside rotation.

    Rotation is per database name, so a renamed database orphans its old dumps:
    they are never aged out and never refreshed. That is worth surfacing — a
    stale file sitting in the backup directory reads as a backup.
    """
    if not BACKUP_DIR.exists():
        return {"error": f"{BACKUP_DIR} does not exist"}
    newest: dict[str, dict[str, Any]] = {}
    for f in BACKUP_DIR.glob("*.json.gz"):
        db = f.name.rsplit("-", 1)[0]
        stat = f.stat()
        age_h = (datetime.now(UTC).timestamp() - stat.st_mtime) / 3600
        row = {"file": f.name, "age_hours": round(age_h, 1), "bytes": stat.st_size}
        if db not in newest or stat.st_mtime > newest[db]["_mtime"]:
            newest[db] = {**row, "_mtime": stat.st_mtime}
    live = {"the_brain", "hobby"}
    out: dict[str, Any] = {"databases": {}, "orphaned": []}
    for db, row in newest.items():
        row.pop("_mtime", None)
        if db in live:
            row["stale"] = row["age_hours"] > BACKUP_STALE_HOURS
            out["databases"][db] = row
        else:
            out["orphaned"].append({"database": db, **row})
    return out


def vault() -> dict[str, Any]:
    """Document counts and the LiveSync pain signals."""
    env = registry.read_env(Path(__file__).resolve().parent.parent / ".env")
    url, user, pw = (
        env.get("VAULT_COUCHDB_URL"),
        env.get("VAULT_USER"),
        env.get("VAULT_COUCHDB_PASSWORD"),
    )
    if not (url and user and pw):
        return {"error": "vault credentials not configured in homelab/.env"}
    import base64

    auth = base64.b64encode(f"{user}:{pw}".encode()).decode()

    def api(path: str) -> Any | None:
        req = urllib.request.Request(url.rstrip("/") + path, headers={"Authorization": f"Basic {auth}"})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                return json.loads(r.read().decode())
        except Exception:  # noqa: BLE001
            return None

    dbs = api("/_all_dbs") or []
    out: dict[str, Any] = {"databases": {}}
    for db in [d for d in dbs if not d.startswith("_")]:
        info = api(f"/{db}")
        if not info:
            continue
        row: dict[str, Any] = {"docs": info.get("doc_count"), "deleted": info.get("doc_del_count")}
        rows = (api(f"/{db}/_all_docs?conflicts=true&include_docs=true&limit=2000") or {}).get("rows", [])
        row["conflicts"] = sum(1 for r in rows if (r.get("doc") or {}).get("_conflicts"))
        # The duplicate-note race the reaper cleans up: "note 2.md" *beside* an
        # existing "note.md". The suffix alone means nothing — `10 raw/` is full
        # of legitimate " 1"/" 2" files straight from the Web Clipper — so only
        # a suffixed note whose base also exists is counted.
        ids = {r.get("id", "") for r in rows}
        row["duplicate_suffixed"] = sum(
            1
            for i in ids
            if (m := re.match(r"^(.*) \d+\.md$", i)) and f"{m.group(1)}.md" in ids
        )
        row["sampled"] = len(rows)
        out["databases"][db] = row
    return out


# ── slow signals ─────────────────────────────────────────────────────────────

_PYTEST = re.compile(r"(?:(\d+) failed[^\n]*?)?(\d+) passed(?:[^\n]*?(\d+) skipped)?")


def run_tests(app: App) -> dict[str, Any]:
    path = registry.repo_path(app)
    pytest = path / (app.venv or "") / "bin" / "pytest" if app.venv else None
    if not pytest or not pytest.exists():
        return {"ran": False, "reason": "no local venv — not run here"}
    started = datetime.now(UTC)
    rc, out = _run([str(pytest), "-q"], cwd=path, timeout=TEST_TIMEOUT)
    m = _PYTEST.search(out)
    result: dict[str, Any] = {
        "ran": True,
        "ok": rc == 0,
        "passed": int(m.group(2)) if m else None,
        "failed": int(m.group(1)) if m and m.group(1) else 0,
        "duration_s": round((datetime.now(UTC) - started).total_seconds(), 1),
        "collected_at": now(),
    }
    if not m:
        # Non-zero with nothing parseable is not "0 failures" — it is a suite
        # that never reported. Saying which is the difference between a row you
        # can act on and one that reads as a phantom failure.
        result["reason"] = "timed out" if rc == 124 else f"pytest exited {rc} with no summary"
    return result


def outdated(app: App) -> dict[str, Any]:
    path = registry.repo_path(app)
    uv = os.environ.get("UV_BIN", str(Path.home() / ".local" / "bin" / "uv"))
    if not app.venv or not (path / app.venv).exists() or not Path(uv).exists():
        return {"checked": False}
    rc, out = _run([uv, "pip", "list", "--outdated"], cwd=path, timeout=180)
    if rc != 0:
        return {"checked": False, "error": out.strip()[:120]}
    lines = [ln for ln in out.splitlines()[2:] if ln.strip()]
    return {
        "checked": True,
        "count": len(lines),
        "sample": [ln.split()[0] for ln in lines[:5]],
        "collected_at": now(),
    }


# ── orchestration ────────────────────────────────────────────────────────────


def collect_fast() -> dict[str, Any]:
    first = APPS[0]
    target = registry.ssh_target(first)
    host, port = target if target else ("", "22")
    boxes = containers(host, port) if host else {"error": "no ssh target configured"}
    tags = sorted({c.get("image") for c in boxes.values() if isinstance(c, dict) and c.get("image")})
    labels = image_labels(host, port, tags) if host and tags else {}

    def one(app: App) -> tuple[str, dict[str, Any]]:
        box = boxes.get(app.container, {}) if isinstance(boxes, dict) else {}
        return app.name, {
            "health": probe_health(app),
            "extra": probe_extra(app),
            "container": box or {"state": "absent"},
            "revision": deployed_revision(app, box, labels),
            "repo": repo_state(app),
            "config": shipped_config_drift(app, host, port) if host else None,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        apps = dict(pool.map(one, APPS))

    return {
        "collected_at": now(),
        "apps": apps,
        "backups": backups(),
        "vault": vault(),
    }


def collect_slow() -> dict[str, Any]:
    tests: dict[str, Any] = {}
    packages: dict[str, Any] = {}
    for app in APPS:
        tests[app.name] = run_tests(app)
        packages[app.name] = outdated(app)
    return {"collected_at": now(), "tests": tests, "packages": packages}


def _read(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(path: Path, payload: dict[str, Any]) -> None:
    """Atomic within a tier: write beside, then rename over."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def load() -> dict[str, Any]:
    """Both tiers, merged for reading. Either may be missing or older."""
    out: dict[str, Any] = {}
    if fast := _read(FAST_FILE):
        out["fast"] = fast
    if slow := _read(SLOW_FILE):
        out["slow"] = slow
    return out


def _lock(tier: str):
    """Refuse to run a tier twice at once.

    Not just wasteful: family_calendar's suite writes to fixed paths under its
    repo, so two collectors running it together wedge each other until the
    timeout — which then reports as a failing suite. A scheduled run overlapping
    a manual one is the ordinary way that happens.
    """
    import fcntl

    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    handle = (STATUS_DIR / f".{tier}.lock").open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fast", action="store_true", help="health, containers, drift, backups")
    ap.add_argument("--slow", action="store_true", help="test suites and outdated packages")
    args = ap.parse_args()
    if not (args.fast or args.slow):
        args.fast = True

    written = []
    for tier, enabled, gather, target in (
        ("fast", args.fast, collect_fast, FAST_FILE),
        ("slow", args.slow, collect_slow, SLOW_FILE),
    ):
        if not enabled:
            continue
        held = _lock(tier)
        if held is None:
            print(f"{tier}: another collector is already running — skipped")
            continue
        try:
            _write(target, gather())
            written.append(str(target))
        finally:
            held.close()
    if not written:
        return 0
    print("wrote " + ", ".join(written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
