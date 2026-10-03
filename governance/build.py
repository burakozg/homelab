#!/usr/bin/env python3
"""Build the governance site: status, architecture and models, as static files.

    ./build.py                 write deploy-out/site/
    ./build.py --out DIR       write somewhere else

Status is generated from the collector's last snapshot (homelab/status), so it is
only ever as fresh as that run — every age on the page is re-computed in the
browser against the viewer's clock, and a banner appears when the snapshot is
older than the collector's own stale threshold. Architecture is hand-maintained
(content/architecture.*). The models page joins two things: the model ids found
in the repos' config files (so it cannot drift from what is configured) and a
hand-maintained hardware table, flagging any configured id the table does not
cover.

Nothing here knows a hostname or address; the site holds no secrets, and the
hosting side (nginx behind Traefik's login gate) is configured in ./deploy.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "status"))

import registry  # noqa: E402
from collect import load  # noqa: E402
from render import AGE_JS, FONTS, STYLE, e, render_body  # noqa: E402

PAGES = (
    ("index.html", "/", "Status"),
    ("architecture.html", "/architecture", "Architecture"),
    ("models.html", "/models", "Models"),
)

NAV_CSS = """
body{margin:0;background:var(--paper);color:var(--ink);font-family:"IBM Plex Sans",system-ui,sans-serif;-webkit-font-smoothing:antialiased;}
nav.gov{display:flex;align-items:center;gap:6px;padding:10px 26px;border-bottom:1px solid var(--line);background:var(--panel);
  font-family:"IBM Plex Mono",monospace;font-size:12px;position:sticky;top:0;z-index:5;}
nav.gov b{letter-spacing:.14em;text-transform:uppercase;color:var(--ink-3);font-size:10.5px;margin-right:14px;font-weight:600;}
nav.gov a{color:var(--ink-2);text-decoration:none;padding:5px 12px;border-radius:6px;}
nav.gov a:hover{background:var(--panel-2);color:var(--ink);}
nav.gov a[aria-current]{background:var(--panel-2);color:var(--ink);font-weight:600;}
#stale{display:none;background:var(--bad-bg);color:var(--bad);border-bottom:1px solid var(--bad);padding:9px 26px;font-size:13px;font-weight:500;}
#stale.on{display:block;}
td.mono{font-family:"IBM Plex Mono",monospace;font-size:12.5px;}
sup.fn{color:var(--warn);font-weight:600;cursor:default;}
p.lede{margin:7px 0 0;color:var(--ink-2);font-size:15px;max-width:72ch;line-height:1.45;}
"""

STALE_JS = """
(function(){
  var el=document.querySelector("[data-ts][data-stale-min]"),b=document.getElementById("stale");
  if(!el||!b)return;
  function check(){
    var m=(Date.now()-Date.parse(el.getAttribute("data-ts")))/60000;
    if(m>+el.getAttribute("data-stale-min")){
      b.textContent="This page has not been refreshed for "+el.textContent.replace(" ago","")+
        " — the collector on the Mac has not published. Treat everything below as old.";
      b.classList.add("on");
    }else b.classList.remove("on");
  }
  check(); setInterval(check,60000);
})();
"""


def page(current: str, title: str, css: str, body: str, js: str = "") -> str:
    links = "".join(
        f'<a href="{href}"{" aria-current=\"page\"" if f == current else ""}>{label}</a>'
        for f, href, label in PAGES
    )
    return (
        f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{e(title)} · Homelab</title>\n{FONTS}\n"
        f"<style>\n{STYLE}\n{css}\n{NAV_CSS}</style></head><body>\n"
        f'<nav class="gov"><b>Homelab</b>{links}</nav>\n'
        f'<div id="stale" role="alert"></div>\n{body}\n'
        f"<script>{AGE_JS}\n{js}</script></body></html>\n"
    )


def status_page() -> str:
    snap = load()
    if not snap:
        body = '<div class="wrap"><section><h2>No snapshot</h2><p>Run ./status.sh first.</p></section></div>'
    else:
        _, body = render_body(snap)
    return page("index.html", "Status", "", body, STALE_JS)


def architecture_page() -> str:
    css = (HERE / "content" / "architecture.css").read_text()
    body = (HERE / "content" / "architecture.html").read_text()
    body = body.replace("@@CODEBASES@@", str(len({a.repo for a in registry.APPS})))
    body = body.replace("@@INSTANCES@@", str(len(registry.APPS)))
    return page("architecture.html", "Architecture", css, body)


# model, generation, rerank, embedding, accuracy_model and friends all end up
# as `<key>: <id>`; ASR and TTS ids carry no slash, so the value test is "looks
# like an id" rather than "contains a vendor".
_KEY = re.compile(r"^\s*(?:-\s*)?(\w*model\w*|generation|rerank|embedding)\s*:\s*([A-Za-z][\w./:-]*)\s*(?:#.*)?$")


def configured_models(projects: Path) -> list[tuple[str, str, str]]:
    """(where, key, model id) for every model named in a repo's config.yaml."""
    found: list[tuple[str, str, str]] = []
    patterns = ("*/config.yaml", "*/backend/config.yaml", "*/instances/*/config.yaml")
    paths = sorted({p for pat in patterns for p in projects.glob(pat)})
    for path in paths:
        rel = path.relative_to(projects)
        where = "/".join(x for x in rel.parts[:-1] if x != "backend")
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#"):
                continue
            m = _KEY.match(line)
            if m and m.group(2) not in ("null", "true", "false"):
                found.append((where, m.group(1), m.group(2).removeprefix("openrouter/")))
    return found


def models_page() -> str:
    hardware = (HERE / "content" / "models-hardware.html").read_text()
    plain = html.unescape(re.sub(r"<[^>]+>", " ", hardware)).lower()

    rows, undocumented = [], 0
    seen: set[tuple[str, str]] = set()
    for where, key, model in configured_models(registry.PROJECTS):
        if (where, model) in seen:
            continue
        seen.add((where, model))
        known = re.search(rf"(?<![\w./-]){re.escape(model.lower())}(?![\w.-])", plain) is not None
        undocumented += not known
        chip = '<span class="chip good">in table</span>' if known else '<span class="chip warn">not in table</span>'
        rows.append(
            f'<tr><th scope="row">{e(where)}</th><td class="mono">{e(key)}</td>'
            f'<td class="mono">{e(model)}</td><td>{chip}</td></tr>'
        )

    gap = (
        f'<span class="chip warn">{undocumented} not covered by the hardware table</span>'
        if undocumented
        else '<span class="chip good">all covered by the hardware table</span>'
    )
    body = f"""<div class="wrap">
<header>
  <div>
    <p class="eyebrow">Homelab · models</p>
    <h1>Which models run where, and what they would need</h1>
    <p class="lede">Read from each repo's <code>config.yaml</code> when this site was built, then
    compared against a hand-maintained hardware table. A deploy-time environment variable can still
    override a config value on the NAS — the status page, not this one, reports on the running state.</p>
  </div>
</header>
<section class="scroll">
  <h2>Configured today <em>{len(rows)} entries · {gap}</em></h2>
  <table>
    <thead><tr><th>app</th><th>setting</th><th>model</th><th>hardware table</th></tr></thead>
    <tbody>
{chr(10).join(rows) or '<tr><td colspan="4" class="none">no config files found</td></tr>'}
    </tbody>
  </table>
</section>
{hardware}
</div>"""
    return page("models.html", "Models", "", body)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=HERE / "deploy-out" / "site")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    built = {
        "index.html": status_page(),
        "architecture.html": architecture_page(),
        "models.html": models_page(),
    }
    for name, text in built.items():
        (args.out / name).write_text(text, encoding="utf-8")
    (args.out / "built-at.txt").write_text(datetime.now(UTC).isoformat() + "\n")
    print(f"✓ built {len(built)} pages → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
