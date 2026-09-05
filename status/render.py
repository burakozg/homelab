"""Turn a snapshot into the status page.

Information design, not a document: the verdict comes first, state is encoded as
form as well as number, and every section says when it was gathered. Freshness is
rendered as loudly as health, because the failure mode of a dashboard is not
being wrong — it is being *old* and looking current.

Semantic colour (good / warn / bad) is deliberately separate from the accent
hue, so "needs attention" never depends on remembering what the accent means.
"""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from typing import Any

#: Anything older than this is drawn as stale rather than reported as fact.
STALE_MINUTES = {"fast": 90, "slow": 36 * 60}


def _age(iso: str | None) -> tuple[float, str]:
    """(minutes, human) since an ISO timestamp."""
    if not iso:
        return (float("inf"), "never")
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return (float("inf"), "unknown")
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    mins = (datetime.now(UTC) - then).total_seconds() / 60
    if mins < 1:
        return (mins, "just now")
    if mins < 90:
        return (mins, f"{int(mins)} min ago")
    if mins < 60 * 36:
        return (mins, f"{int(mins / 60)} h ago")
    return (mins, f"{int(mins / 1440)} d ago")


def e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _chip(text: str, tone: str) -> str:
    return f'<span class="chip {tone}">{e(text)}</span>'


def _app_rows(fast: dict[str, Any]) -> str:
    rows = []
    for name, a in sorted(fast.get("apps", {}).items()):
        health, box = a.get("health") or {}, a.get("container") or {}
        rev, repo = a.get("revision") or {}, a.get("repo") or {}

        if not health.get("probed"):
            hchip = _chip("no endpoint", "muted")
        elif health.get("ok"):
            hchip = _chip("healthy", "good")
        else:
            verdict = health_verdict(a)
            tone = verdict[0] if verdict else "bad"
            hchip = _chip("not responding" if tone == "warn" else (health.get("error") or "degraded"), tone)

        state = box.get("state", "absent")
        schip = _chip(state, "good" if state == "running" else "bad")
        restarts = box.get("restarts") or 0
        if restarts:
            schip += " " + _chip(f"{restarts}× restarted", "warn")

        _, started = _age(box.get("started_at"))

        rstate = rev.get("state")
        if rstate == "in-sync":
            rchip = _chip("in sync", "good")
        elif rstate == "dirty":
            rchip = _chip("built from dirty tree", "warn")
        elif rstate == "behind":
            n = rev.get("commits_ahead")
            rchip = _chip(f"{n} commit{'s' if n != 1 else ''} behind" if n else "behind", "warn")
        else:
            rchip = _chip("unknown", "muted")

        flags = []
        if repo.get("dirty"):
            flags.append(_chip(f"{repo['dirty']} uncommitted", "warn"))
        if repo.get("unpushed"):
            flags.append(_chip(f"{repo['unpushed']} unpushed", "warn"))
        cfg = a.get("config") or {}
        if cfg.get("state") == "drifted":
            flags.append(_chip("config edited on NAS", "bad"))
        vdb = a.get("vault_db") or {}
        if vdb.get("state") == "wrong":
            flags.append(_chip("wrong vault db", "bad"))
        elif vdb.get("state") == "unknown":
            flags.append(_chip("vault db unconfirmed", "warn"))

        rows.append(
            f"""<tr>
  <th scope="row">{e(name)}<small>{e(repo.get('branch') or '')}</small></th>
  <td>{hchip}</td>
  <td>{schip}<small>up {e(started)}</small></td>
  <td>{rchip}</td>
  <td class="flags">{' '.join(flags) or '<span class="none">—</span>'}</td>
</tr>"""
        )
    return "\n".join(rows)


def _vault_rows(fast: dict[str, Any]) -> str:
    rows = []
    for db, r in sorted((fast.get("vault", {}).get("databases") or {}).items()):
        live, tombs = r.get("live"), r.get("tombstones")
        # Live notes and tombstones in one cell, because CouchDB's own
        # doc_count is the sum of both and reads as a note count when it is not.
        notes = f"{live:,}" if isinstance(live, int) else "—"
        extra = f"<small>+{tombs:,} deleted</small>" if tombs else ""
        conf, dupes = r.get("conflicts") or 0, r.get("duplicate_suffixed") or 0
        rows.append(
            f'<tr><th scope="row">{e(db)}</th><td>{notes}{extra}</td>'
            f"<td>{_chip(str(conf), 'good' if not conf else 'bad')}</td>"
            f"<td>{_chip(str(dupes), 'good' if not dupes else 'warn')}</td></tr>"
        )
    return "\n".join(rows)


def _backup_rows(fast: dict[str, Any]) -> str:
    rows = []
    for db, r in sorted((fast.get("backups", {}).get("databases") or {}).items()):
        age = _chip(f"{r['age_hours']:.0f} h old", "bad" if r.get("stale") else "good")
        rows.append(
            f'<tr><th scope="row">{e(db)}</th><td>{age}</td>'
            f"<td>{r['bytes'] / 1e6:.1f} MB</td>"
            f"<td><small>{e(r['file'])}</small></td></tr>"
        )
    return "\n".join(rows)


#: A probe that timed out against a *running* container is a different fact
#: from one that was refused. vault-ask rebuilds its whole index on start and
#: stalls its event loop for minutes — answering 200 in its own log while every
#: probe here times out. Calling that "unhealthy" in red, every time it
#: restarts, is how a page teaches you to scroll past red.
def health_verdict(app: dict[str, Any]) -> tuple[str, str] | None:
    """(tone, description), or None when the app is fine or has no endpoint."""
    health = app.get("health") or {}
    if not health.get("probed") or health.get("ok"):
        return None
    running = (app.get("container") or {}).get("state") == "running"
    error = str(health.get("error") or "")
    if running and "Timeout" in error:
        return ("warn", "not responding — busy or wedged, container is up")
    if health.get("failing_checks"):
        return ("bad", "reports " + ", ".join(health["failing_checks"]) + " failing")
    return ("bad", f"unhealthy — {error or 'check failed'}")


def _feed_alerts(extra: dict[str, Any]) -> list[tuple[str, str]]:
    """Feeds that are actually broken, not ones that had a bad afternoon.

    The last run's `feeds_failed` is a poor alert: the run that reported 8 of 23
    took twenty minutes where a healthy one takes eighty-five seconds, and the
    very next poll had zero failures with every feed's `consecutive_failures`
    still at 0. That was the network, not the feeds — and a dashboard that
    cannot tell the two apart teaches you to ignore it.

    So the alert keys off the per-podcast state CouchDB actually keeps:
    `consecutive_failures`, and `circuit_open` once the app has given up on a
    feed. A single bad poll is reported as context, never as a problem.
    """
    alerts: list[tuple[str, str]] = []
    feeds = (extra.get("status") or {}).get("feeds") or []
    if isinstance(feeds, list):
        broken = [f for f in feeds if f.get("circuit_open")]
        failing = [f for f in feeds if (f.get("consecutive_failures") or 0) >= 3]
        if broken:
            names = ", ".join(sorted(f.get("slug", "?") for f in broken)[:4])
            alerts.append(("bad", f"podcast-digest: {len(broken)} feed(s) given up on — {names}"))
        for f in sorted(failing, key=lambda x: x.get("slug", ""))[:4]:
            if f.get("circuit_open"):
                continue
            alerts.append(
                ("warn", f"podcast-digest: {f.get('slug')} has failed "
                         f"{f['consecutive_failures']} polls in a row")
            )

    ing = ((extra.get("runs") or {}).get("jobs") or {}).get("ingest", {}).get("summary", {})
    failed, polled = ing.get("feeds_failed"), ing.get("feeds_polled")
    if failed and not alerts:
        # Context, not an alarm: nothing is persistently failing, so this was a
        # single rough poll and the next one will most likely be clean.
        alerts.append(
            ("info", f"podcast-digest: {failed} of {polled} feeds timed out on one poll "
                     "(no feed is persistently failing)")
        )
    return alerts


def _attention(fast: dict[str, Any], slow: dict[str, Any]) -> list[tuple[str, str]]:
    """The things actually worth acting on, worst first.

    A dashboard whose green rows outnumber its real problems buries them, so
    this list exists to be the first thing read — and to be empty most days.
    """
    out: list[tuple[str, str]] = []
    for name, a in sorted(fast.get("apps", {}).items()):
        box = a.get("container") or {}
        if verdict := health_verdict(a):
            tone, description = verdict
            out.append((tone, f"{name} {description}"))
        if box.get("state") not in ("running", None):
            out.append(("bad", f"{name} container is {box.get('state')}"))
        if (a.get("config") or {}).get("state") == "drifted":
            out.append(("bad", f"{name}: config.yaml on the NAS differs from the repo"))
        vdb = a.get("vault_db") or {}
        if vdb.get("state") == "wrong":
            found = ", ".join(f"{k}={v}" for k, v in (vdb.get("found") or {}).items())
            out.append(
                ("bad", f"{name} is writing to the wrong vault database — "
                        f"expected {vdb.get('expected')}, has {found}")
            )
        elif vdb.get("state") == "unknown":
            out.append(("warn", f"{name}: could not confirm which vault database it writes to"))

    out += _feed_alerts((fast.get("apps", {}).get("podcast-digest") or {}).get("extra", {}))

    vd = (fast.get("apps", {}).get("video-digest") or {}).get("extra", {}).get("metrics", {})
    failed_jobs = (vd.get("jobs") or {}).get("failed") or 0
    total, written = vd.get("videos_total"), (vd.get("write_stage") or {}).get("done")
    unwritten = (total - written) if isinstance(total, int) and isinstance(written, int) else None
    if unwritten:
        out.append(("bad", f"video-digest: {unwritten} video(s) never got a note"))
    elif failed_jobs:
        # A failed *attempt* on a video that is written is history, not a
        # problem: the one on record is a YouTube 429 on the caption fetch,
        # after which the retry succeeded and the note landed. Counting it as an
        # open failure means the row never clears.
        out.append(
            ("info", f"video-digest: {failed_jobs} failed attempt(s), all videos written")
        )
    if vd.get("pending_asr"):
        out.append(("info", f"video-digest: {vd['pending_asr']} waiting on transcription"))

    for db, row in (fast.get("backups", {}).get("databases") or {}).items():
        if row.get("stale"):
            out.append(("bad", f"backup of {db} is {row['age_hours']:.0f} h old"))
    orphans = fast.get("backups", {}).get("orphaned") or []
    if orphans:
        names = ", ".join(o["database"] for o in orphans)
        out.append(("warn", f"backup dumps outside rotation: {names} — never refreshed, never aged out"))

    for db, row in (fast.get("vault", {}).get("databases") or {}).items():
        if row.get("conflicts"):
            out.append(("bad", f"{db}: {row['conflicts']} document conflict(s)"))
        if row.get("duplicate_suffixed"):
            out.append(
                ("warn", f"{db}: {row['duplicate_suffixed']} duplicate notes beside their original")
            )

    for name, t in sorted((slow.get("tests") or {}).items()):
        if t.get("ran") and not t.get("ok"):
            detail = (
                f"{t['failed']} test(s) failing"
                if t.get("failed")
                else t.get("reason") or "suite did not pass"
            )
            out.append(("bad", f"{name}: {detail}"))

    order = {"bad": 0, "warn": 1, "info": 2}
    return sorted(out, key=lambda x: order.get(x[0], 3))


def render(snapshot: dict[str, Any]) -> str:
    fast = snapshot.get("fast") or {}
    slow = snapshot.get("slow") or {}
    fast_mins, fast_age = _age(fast.get("collected_at"))
    slow_mins, slow_age = _age(slow.get("collected_at"))
    fast_stale = fast_mins > STALE_MINUTES["fast"]
    slow_stale = slow_mins > STALE_MINUTES["slow"]

    apps = fast.get("apps", {})
    healthy = sum(
        1
        for a in apps.values()
        if (a.get("health") or {}).get("ok") or not (a.get("health") or {}).get("probed")
    )
    running = sum(1 for a in apps.values() if (a.get("container") or {}).get("state") == "running")
    attention = _attention(fast, slow)

    tests = slow.get("tests") or {}
    passed = sum(t.get("passed") or 0 for t in tests.values() if t.get("ran"))
    failed = sum(t.get("failed") or 0 for t in tests.values() if t.get("ran"))
    pkgs = sum((p.get("count") or 0) for p in (slow.get("packages") or {}).values())

    # `info` lines are context, so a page carrying only those still reads as
    # good — otherwise every transient blip permanently downgrades the verdict.
    actionable = [t for t, _ in attention if t in ("bad", "warn")]
    verdict = "bad" if "bad" in actionable else ("warn" if actionable else "good")
    verdict_text = {
        "good": "Everything is where it should be",
        "warn": "Running, with things worth a look",
        "bad": "Something needs attention",
    }[verdict]

    att_html = "\n".join(
        f'<li class="{t}"><span class="dot"></span>{e(m)}</li>' for t, m in attention
    ) or '<li class="good"><span class="dot"></span>Nothing outstanding.</li>'
    if not actionable and attention:
        att_html = ('<li class="good"><span class="dot"></span>Nothing needs action.</li>\n'
                    + att_html)

    backup_rows = _backup_rows(fast) or '<tr><td colspan="4" class="none">no backups found</td></tr>'

    vault_rows = _vault_rows(fast) or '<tr><td colspan="4" class="none">vault not reachable</td></tr>'

    test_rows = []
    for name in sorted(set(tests) | set(slow.get("packages") or {})):
        t = tests.get(name, {})
        p = (slow.get("packages") or {}).get(name, {})
        if t.get("ran"):
            if t.get("passed") is None:
                label = t.get("reason") or "no result"
            else:
                label = f"{t['passed']} passed" + (f", {t['failed']} failed" if t.get("failed") else "")
            cell = _chip(label, "good" if t.get("ok") else "bad")
            dur = f"<small>{t.get('duration_s')}s</small>"
        else:
            cell, dur = _chip(t.get("reason") or "not run", "muted"), ""
        pkg = (
            _chip(f"{p['count']} outdated", "warn" if p.get("count") else "good")
            if p.get("checked")
            else _chip("—", "muted")
        )
        test_rows.append(
            f'<tr><th scope="row">{e(name)}</th><td>{cell}{dur}</td><td>{pkg}</td></tr>'
        )

    return f"""<title>Homelab Status</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
:root{{
  --paper:#f5f7f8;--panel:#fff;--panel-2:#eef3f5;--ink:#14202a;--ink-2:#4a5c6a;--ink-3:#7b8d99;
  --line:#d3dde3;--accent:#1f7a6d;
  --good:#1f7a4d;--good-bg:#e6f4ec;--warn:#a2701a;--warn-bg:#fbf1dd;--bad:#b23a2f;--bad-bg:#fbeae8;
  --muted:#6b7d89;--muted-bg:#edf1f3;
}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{
  --paper:#0d1519;--panel:#131f26;--panel-2:#182731;--ink:#e6eef2;--ink-2:#93a7b3;--ink-3:#6d8290;
  --line:#24343d;--accent:#4fc0ae;
  --good:#5cc98d;--good-bg:#12291d;--warn:#e0b055;--warn-bg:#2b2412;--bad:#f08a7d;--bad-bg:#2e1a18;
  --muted:#8ea2ae;--muted-bg:#1b2831;
}}}}
:root[data-theme="dark"]{{
  --paper:#0d1519;--panel:#131f26;--panel-2:#182731;--ink:#e6eef2;--ink-2:#93a7b3;--ink-3:#6d8290;
  --line:#24343d;--accent:#4fc0ae;
  --good:#5cc98d;--good-bg:#12291d;--warn:#e0b055;--warn-bg:#2b2412;--bad:#f08a7d;--bad-bg:#2e1a18;
  --muted:#8ea2ae;--muted-bg:#1b2831;
}}
body{{background:var(--paper);color:var(--ink);font-family:"IBM Plex Sans",system-ui,sans-serif;-webkit-font-smoothing:antialiased;}}
.wrap{{max-width:1180px;margin:0 auto;padding:26px 26px 40px;display:flex;flex-direction:column;gap:20px;}}
header{{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;flex-wrap:wrap;}}
.eyebrow{{font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-3);margin:0 0 6px;}}
h1{{margin:0;font-size:27px;font-weight:600;letter-spacing:-.015em;text-wrap:balance;}}
h1 .mark{{color:var(--{verdict});}}
.freshness{{display:flex;gap:18px;font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--ink-3);}}
.freshness b{{display:block;font-size:15px;font-family:"IBM Plex Sans";font-weight:600;color:var(--ink);margin-top:3px;}}
.freshness .stale b{{color:var(--bad);}}
section{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 18px;}}
h2{{margin:0 0 12px;font-family:"IBM Plex Mono",monospace;font-size:10.5px;font-weight:600;letter-spacing:.16em;text-transform:uppercase;color:var(--ink-3);display:flex;justify-content:space-between;}}
h2 em{{font-style:normal;text-transform:none;letter-spacing:.03em;}}
ul.attention{{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:9px;}}
ul.attention li{{display:flex;align-items:baseline;gap:10px;font-size:14px;color:var(--ink-2);}}
ul.attention .dot{{width:8px;height:8px;border-radius:50%;flex:none;transform:translateY(-1px);}}
li.bad .dot{{background:var(--bad);}} li.warn .dot{{background:var(--warn);}}
li.good .dot{{background:var(--good);}} li.info .dot{{background:var(--muted);}}
li.info{{color:var(--ink-3);}}
li.bad{{color:var(--ink);font-weight:500;}}
.tables{{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:20px;}}
table{{width:100%;border-collapse:collapse;font-size:13.5px;}}
th,td{{text-align:left;padding:8px 10px 8px 0;border-bottom:1px solid var(--line);vertical-align:top;}}
tr:last-child th,tr:last-child td{{border-bottom:none;}}
thead th{{font-family:"IBM Plex Mono",monospace;font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-3);font-weight:600;}}
tbody th{{font-weight:600;font-family:"IBM Plex Mono",monospace;font-size:12.5px;white-space:nowrap;}}
small{{display:block;color:var(--ink-3);font-size:11px;margin-top:3px;font-family:"IBM Plex Mono",monospace;}}
td{{font-variant-numeric:tabular-nums;}}
.chip{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11.5px;font-weight:500;white-space:nowrap;}}
.chip.good{{background:var(--good-bg);color:var(--good);}}
.chip.warn{{background:var(--warn-bg);color:var(--warn);}}
.chip.bad{{background:var(--bad-bg);color:var(--bad);}}
.chip.muted{{background:var(--muted-bg);color:var(--muted);}}
.flags{{line-height:2;}} .none{{color:var(--ink-3);}}
.scroll{{overflow-x:auto;}}
footer{{color:var(--ink-3);font-size:12px;line-height:1.6;}}
footer code{{font-family:"IBM Plex Mono",monospace;color:var(--ink-2);}}
</style>
<div class="wrap">
<header>
  <div>
    <p class="eyebrow">Homelab · {len(apps)} applications</p>
    <h1><span class="mark">●</span> {e(verdict_text)}</h1>
  </div>
  <div class="freshness">
    <div class="{'stale' if fast_stale else ''}"><span>live signals</span><b>{e(fast_age)}</b></div>
    <div class="{'stale' if slow_stale else ''}"><span>tests &amp; packages</span><b>{e(slow_age)}</b></div>
    <div><span>healthy / running</span><b>{healthy}/{len(apps)} · {running}/{len(apps)}</b></div>
    <div><span>tests</span><b>{passed:,} pass{f' · {failed} fail' if failed else ''}</b></div>
  </div>
</header>

<section>
  <h2>Worth attention</h2>
  <ul class="attention">
{att_html}
  </ul>
</section>

<section class="scroll">
  <h2>Applications <em>{e(fast_age)}</em></h2>
  <table>
    <thead><tr><th>app</th><th>health</th><th>container</th><th>deployed code</th><th>repo</th></tr></thead>
    <tbody>
{_app_rows(fast)}
    </tbody>
  </table>
</section>

<div class="tables">
  <section class="scroll">
    <h2>Backups <em>{e(fast_age)}</em></h2>
    <table>
      <thead><tr><th>database</th><th>age</th><th>size</th><th>file</th></tr></thead>
      <tbody>
{backup_rows}
      </tbody>
    </table>
  </section>
  <section class="scroll">
    <h2>Vault <em>{e(fast_age)}</em></h2>
    <table>
      <thead><tr><th>database</th><th>live notes</th><th>conflicts</th><th>duplicates</th></tr></thead>
      <tbody>
{vault_rows}
      </tbody>
    </table>
  </section>
</div>

<section class="scroll">
  <h2>Tests and dependencies <em>{e(slow_age)}{' — stale' if slow_stale else ''}</em></h2>
  <table>
    <thead><tr><th>repo</th><th>suite</th><th>packages</th></tr></thead>
    <tbody>
{chr(10).join(test_rows) or '<tr><td colspan="3" class="none">no slow run recorded yet</td></tr>'}
    </tbody>
  </table>
</section>

<footer>
  Collected on the Mac by <code>homelab/status</code> — the only vantage point that sees both
  the running containers and the source they were built from. Live signals refresh every
  30&nbsp;minutes; tests and package checks run daily and are cached, so each carries its own
  timestamp above. <strong>An age shown in red means the collector has not run, not that the
  value is bad.</strong> Package counts are outdated-versions, not vulnerabilities.
</footer>
</div>
"""


def main() -> int:
    import argparse

    from collect import SNAPSHOT, load

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", default=str(SNAPSHOT.parent / "status.html"))
    args = ap.parse_args()
    snap = load()
    if not snap:
        print(f"no snapshot in {SNAPSHOT.parent} — run ./status.sh first")
        return 1
    from pathlib import Path

    Path(args.out).write_text(render(snap), encoding="utf-8")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
