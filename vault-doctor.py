#!/usr/bin/env python3
"""Find and repair the two vault corruptions that are safe to fix by rule:

  * a zero-byte note — the vault-writer skill's own description of what a
    click on an unqualified `[[wikilink]]` produces when its target has never
    been written: Obsidian creates the note, empty, right there.
  * an EXACT duplicate frontmatter line — two apps (or two concurrent writes
    from the same one) racing LiveSync's line-level merge can duplicate a
    line verbatim rather than collapsing it. Duplicate keys are invalid YAML,
    so Obsidian gives up on the properties panel and shows raw text instead.

Both are mechanical: there is exactly one correct outcome and no judgment
call. A third thing this also finds — two frontmatter lines with the SAME KEY
but DIFFERENT VALUES, and two topic notes whose basenames differ only by
punctuation/case (`threat-locker.md` next to `threatlocker.md`) — is
deliberately never auto-fixed. Two writers disagreeing, or two notes that may
have accumulated genuinely different content, is a question for a person; see
`docs/obsidian-vault-writer` skill, "leave both, where a human can see them."

Uses stdlib only (no venv to keep in sync with the bash scripts beside it).
Reads VAULT_COUCHDB_URL / VAULT_DB / VAULT_USER / VAULT_COUCHDB_PASSWORD from
.env, the same variables backup-vault.sh uses (see .env.example).

Usage:
    ./vault-doctor.py             fix what's mechanically safe, report the rest
    ./vault-doctor.py --check     the same scan, no writes
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent

#: Same scheme podcast-digest/clippings-topics/taster already write chunks
#: under — content-addressed, so a repair that happens to reproduce existing
#: text collides harmlessly with what's already there.
_CHUNK_PREFIX = "h:t"

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)

#: Folders the human writes into directly (Web Clipper saves, tasting notes,
#: ...) rather than an app auto-generating pages into. A zero-byte note here
#: might be an intentional placeholder someone meant to come back to, not a
#: click on a dead `[[wikilink]]` — auto-deleting content nobody but a human
#: put there is not this tool's call to make. Reported instead, never fixed.
_HUMAN_OWNED_PREFIXES = ("10 raw/",)


def _load_env() -> None:
    env_file = HERE / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


class Vault:
    def __init__(self, url: str, db: str, user: str, password: str) -> None:
        self.base = f"{url.rstrip('/')}/{db}"
        auth = base64.b64encode(f"{user}:{password}".encode()).decode()
        self._headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}

    def _request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{self.base}{path}", data=data, headers=self._headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def iter_docs(self, page: int = 500):
        """Every document, a page at a time.

        One `_all_docs?include_docs=true` for the whole database is a 60 MB
        response; read and parsed in one go it peaked at ~520 MB resident. That
        is nothing on a laptop and an OOM-kill on the NAS, where this now runs.
        Paging keeps the raw response and the parsed copy small and short-lived;
        what stays is only what `scan` keeps.
        """
        startkey: str | None = None
        while True:
            query = f"/_all_docs?include_docs=true&limit={page + 1}"
            if startkey is not None:
                query += "&startkey=" + quote(json.dumps(startkey), safe="")
            status, body = self._request("GET", query)
            if status != 200:
                raise SystemExit(f"FATAL: could not list documents: HTTP {status} {body}")
            rows = body["rows"]
            for row in rows[:page]:
                if "doc" in row:
                    yield row["doc"]
            if len(rows) <= page:
                return
            startkey = rows[page]["id"]

    def put(self, doc_id: str, body: dict) -> int:
        status, _ = self._request("PUT", f"/{quote(doc_id, safe='')}", body)
        return status

    def get(self, doc_id: str) -> dict | None:
        status, body = self._request("GET", f"/{quote(doc_id, safe='')}")
        return body if status == 200 else None

    def put_with_retry(self, doc_id: str, build: Callable[[dict | None], dict]) -> bool:
        """``build`` takes the current doc (or None) and returns the next body.

        Same read-modify-write-retry shape every vault client in this vault
        already uses for a conflicting PUT.
        """
        for _ in range(5):
            current = self.get(doc_id)
            status = self.put(doc_id, build(current))
            if status != 409:
                return status in (201, 202)
        return False


def _key(line: str) -> str:
    return line.split(":", 1)[0].strip()


def _chunk_id(content: str) -> str:
    return _CHUNK_PREFIX + hashlib.sha1(content.encode("utf-8")).hexdigest()[:24]  # noqa: S324


def _norm_basename(name: str) -> str:
    ascii_only = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", ascii_only.lower())


def _dedupe_frontmatter(lines: list[str]) -> tuple[list[str], list[str]]:
    """``(fixed lines, dropped exact-duplicate lines)``.

    Only a line byte-identical to one already kept is dropped. A same-key
    line with a different value is left in place, untouched — see the
    module docstring.
    """
    kept: list[str] = []
    dropped: list[str] = []
    for line in lines:
        if line.strip() and line in kept:
            dropped.append(line)
            continue
        kept.append(line)
    return kept, dropped


def scan(vault: Vault) -> dict:
    chunks: dict[str, str] = {}
    entries: list[dict] = []
    for d in vault.iter_docs():
        if d.get("type") == "leaf":
            chunks[d["_id"]] = d["data"]
        elif d.get("type") == "plain" and not d.get("deleted") and "path" in d:
            entries.append(d)

    all_stubs = [e for e in entries if int(e.get("size") or 0) == 0]
    stubs = [e for e in all_stubs if not e["path"].startswith(_HUMAN_OWNED_PREFIXES)]
    human_stubs = [e for e in all_stubs if e["path"].startswith(_HUMAN_OWNED_PREFIXES)]

    dup_key_notes = []  # (entry, dropped_exact_lines, differing_keys)
    for e in entries:
        content = "".join(chunks.get(c, "") for c in (e.get("children") or []))
        match = _FRONTMATTER.match(content)
        if not match:
            continue
        fm_lines = match.group(1).split("\n")
        fixed, dropped = _dedupe_frontmatter(fm_lines)
        counts: dict[str, int] = {}
        for line in fixed:
            if line.strip():
                counts[_key(line)] = counts.get(_key(line), 0) + 1
        differing = sorted(k for k, n in counts.items() if n > 1)
        if dropped or differing:
            new_content = content[: match.start(1)] + "\n".join(fixed) + content[match.end(1) :]
            dup_key_notes.append((e, dropped, differing, new_content))

    topics = [e for e in entries if e["path"].startswith("99 topics/")]
    groups: dict[str, list[str]] = {}
    for e in topics:
        base = e["path"].rsplit("/", 1)[-1].removesuffix(".md")
        groups.setdefault(_norm_basename(base), []).append(base)
    near_dupes = [names for names in groups.values() if len(names) > 1]

    return {
        "stubs": stubs,
        "human_stubs": human_stubs,
        "dup_key_notes": dup_key_notes,
        "near_dupes": near_dupes,
    }


def _summarize(found: dict) -> dict:
    """`scan()`'s result as plain, JSON-serializable data — paths and key
    names only, none of the vault's internal doc fields (`_id`, `_rev`,
    `children`, ...). For `status.sh`, which only ever wants the shape of
    the problem, never the document underneath it."""
    return {
        "stubs": [e["path"] for e in found["stubs"]],
        "human_owned_stubs": [e["path"] for e in found["human_stubs"]],
        "duplicate_key_notes": [
            {
                "path": entry["path"],
                "type": "exact-dup" if dropped else "value-conflict",
                "keys": differing or sorted({_key(l) for l in dropped}),
            }
            for entry, dropped, differing, _new_content in found["dup_key_notes"]
        ],
        "near_duplicate_groups": [sorted(names) for names in found["near_dupes"]],
    }


def main() -> int:
    _load_env()
    args = sys.argv[1:]
    as_json = "--json" in args
    # A dashboard collector must never mutate what it is only reporting on —
    # `--json` is read-only unconditionally, `--check` alone is spelled out
    # only for a human running it by hand.
    check_only = as_json or "--check" in args

    url = os.environ.get("VAULT_COUCHDB_URL", "")
    db = os.environ.get("VAULT_DB", "the_brain")
    user = os.environ.get("VAULT_USER", "admin")
    password = os.environ.get("VAULT_COUCHDB_PASSWORD", "")
    if not url:
        if as_json:
            print(json.dumps({"error": "VAULT_COUCHDB_URL not set (check .env)"}))
            return 0
        print("FATAL: VAULT_COUCHDB_PASSWORD/VAULT_COUCHDB_URL not set (check .env)", file=sys.stderr)
        return 2

    vault = Vault(url, db, user, password)
    found = scan(vault)

    if as_json:
        print(json.dumps({"db": db, **_summarize(found)}))
        return 0

    print(f"vault-doctor: db={db} stubs={len(found['stubs'])} "
          f"human_owned_stubs={len(found['human_stubs'])} "
          f"duplicate-key-notes={len(found['dup_key_notes'])} "
          f"near-duplicate-topic-groups={len(found['near_dupes'])}")

    fixed_stubs = 0
    for entry in found["stubs"]:
        print(f"  stub: {entry['path']}")
        if check_only:
            continue
        ok = vault.put_with_retry(
            entry["_id"],
            lambda current, e=entry: {
                **(current or e),
                "deleted": True,
                "mtime": int(time.time() * 1000),
            },
        )
        if ok:
            fixed_stubs += 1
        else:
            print(f"    FAILED to delete {entry['path']}", file=sys.stderr)

    fixed_dups = 0
    for entry, dropped, differing, new_content in found["dup_key_notes"]:
        tag = "exact-dup" if dropped else "value-conflict"
        print(f"  duplicate-key ({tag}): {entry['path']} keys={differing or [_key(l) for l in dropped]}")
        if not dropped:
            continue  # only a value conflict remains — never auto-fixed
        if check_only:
            continue
        chunk_id = _chunk_id(new_content)
        vault.put(chunk_id, {"_id": chunk_id, "data": new_content, "type": "leaf"})
        ok = vault.put_with_retry(
            entry["_id"],
            lambda current, e=entry, cid=chunk_id, n=new_content: {
                **(current or e),
                "children": [cid],
                "size": len(n.encode("utf-8")),
                "mtime": int(time.time() * 1000),
            },
        )
        if ok:
            fixed_dups += 1
        else:
            print(f"    FAILED to repair {entry['path']}", file=sys.stderr)

    for entry in found["human_stubs"]:
        print(f"  needs a human (empty note in a human-owned folder): {entry['path']}")

    for names in found["near_dupes"]:
        print(f"  needs a human: {[f'99 topics/{n}.md' for n in names]}")

    if check_only:
        print("(--check: no writes made)")
    else:
        print(f"fixed: {fixed_stubs} stub(s) deleted, {fixed_dups} duplicate-key note(s) repaired")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
