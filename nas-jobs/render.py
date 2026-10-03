#!/usr/bin/env python3
"""Write the two files the jobs container needs and git must never hold.

    render.py OUTDIR      → OUTDIR/targets.json   where to probe each app (no secrets)
                            OUTDIR/jobs.env       credentials, mode 0600

Run by ./deploy on the Mac, because only the Mac has the per-project `.deploy.env`
and `.env` files these are resolved from. Nothing is printed that came from them.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "status"))

import registry  # noqa: E402

#: Variables the backup script, vault doctor and vault collector read.
VAULT_VARS = ("VAULT_COUCHDB_URL", "VAULT_USER", "VAULT_COUCHDB_PASSWORD")


def render(out: Path) -> list[str]:
    """Write both files; return a list of problems (empty -> fine)."""
    problems: list[str] = []
    out.mkdir(parents=True, exist_ok=True)

    targets = registry.export_targets()
    (out / "targets.json").write_text(json.dumps(targets, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    home_env = registry.read_env(HERE.parent / ".env")
    lines: list[str] = []
    for key in VAULT_VARS:
        if not home_env.get(key):
            problems.append(f"{key} is missing from homelab/.env")
        else:
            lines.append(f"{key}={home_env[key]}")
    # The database the doctor scans when no job sets one.
    lines.append(f"VAULT_DB={home_env.get('VAULT_DB') or 'the_brain'}")

    for app in registry.APPS:
        target = targets.get(app.name)
        if not (target and target["auth_env"]):
            continue
        value = registry.app_env(app).get(target["auth_env"])
        if value:
            lines.append(f"{target['auth_env']}={value}")
        else:
            problems.append(f"{target['auth_env']} ({app.name}) not found in its repo's .env")

    path = out / "jobs.env"
    # Created 0600 up front: writing then chmod-ing leaves a window where the
    # credentials are readable by anyone on this Mac.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.chmod(path, 0o600)
    return problems


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    problems = render(Path(sys.argv[1]))
    for p in problems:
        print(f"✗ {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
