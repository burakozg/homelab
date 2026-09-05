"""The terminal view of the last snapshot.

Reads what the collector already wrote rather than gathering anything, so it is
instant and works with no network at all. Useful on its own: the page needs a
session to republish, this does not.
"""

from __future__ import annotations

import sys

from collect import SNAPSHOT, load
from render import _age, _attention

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
RED, YELLOW, GREEN = "\033[31m", "\033[33m", "\033[32m"
TONE = {"bad": RED, "warn": YELLOW, "good": GREEN, "info": DIM}


def main() -> int:
    snap = load()
    if not snap:
        print(f"no snapshot in {SNAPSHOT.parent} — run ./status.sh first", file=sys.stderr)
        return 1
    fast, slow = snap.get("fast") or {}, snap.get("slow") or {}
    fast_mins, fast_age = _age(fast.get("collected_at"))
    _, slow_age = _age(slow.get("collected_at"))
    apps = fast.get("apps", {})
    attention = _attention(fast, slow)

    actionable = [t for t, _ in attention if t in ("bad", "warn")]
    worst = "bad" if "bad" in actionable else ("warn" if actionable else "good")
    head = {"good": "all good", "warn": "worth a look", "bad": "needs attention"}[worst]
    stale = f"  {RED}(collected {fast_age} — stale){RESET}" if fast_mins > 90 else f"  {DIM}{fast_age}{RESET}"
    print(f"\n{TONE[worst]}●{RESET} {BOLD}{head}{RESET}{stale}\n")

    for name, a in sorted(apps.items()):
        h, box = a.get("health") or {}, a.get("container") or {}
        rev, repo = a.get("revision") or {}, a.get("repo") or {}
        if not h.get("probed"):
            hs, tone = "no endpoint", DIM
        elif h.get("ok"):
            hs, tone = "healthy", GREEN
        else:
            hs, tone = (h.get("error") or "degraded"), RED
        state = box.get("state", "absent")
        box_tone = GREEN if state == "running" else RED
        notes = []
        if rev.get("state") == "behind":
            notes.append(f"{rev.get('commits_ahead')} commits behind")
        elif rev.get("state") == "unknown":
            notes.append("rev unknown")
        if repo.get("dirty"):
            notes.append(f"{repo['dirty']} uncommitted")
        if repo.get("unpushed"):
            notes.append(f"{repo['unpushed']} unpushed")
        tail = f"  {DIM}{' · '.join(notes)}{RESET}" if notes else ""
        print(f"  {name:<18}{tone}{hs:<14}{RESET}{box_tone}{state:<9}{RESET}{tail}")

    if attention:
        print(f"\n{BOLD}worth attention{RESET}")
        for tone, msg in attention:
            print(f"  {TONE[tone]}•{RESET} {msg}")

    tests = slow.get("tests") or {}
    if tests:
        passed = sum(t.get("passed") or 0 for t in tests.values() if t.get("ran"))
        failed = sum(t.get("failed") or 0 for t in tests.values() if t.get("ran"))
        colour = RED if failed else GREEN
        print(f"\n  tests  {colour}{passed} passed, {failed} failed{RESET}  {DIM}{slow_age}{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
