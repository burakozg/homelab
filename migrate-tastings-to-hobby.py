#!/usr/bin/env python3
"""One-time (re-runnable) copy of taster's data from `tastings` to `hobby`.

Copies, from the shared `tastings` CouchDB database into the new `hobby`
database, everything taster owns:

1. Every LiveSync entry doc under `Tastings/` (path, children, ctime/mtime,
   size, type, eden) plus every chunk (`h:t…`, type "leaf") those entries
   reference.
2. Every plain JSON note doc — the Mango-queryable `{type}:{slug}:{date}`
   documents `couchdb_client.py` writes alongside the LiveSync projection
   (`type` in whisky/cigar/coffee/pipe/beer/raki/chocolate/pairing).

A copy, not a move: `tastings` is left untouched (see the split plan,
Phase 1 step 4 — the old copy stays as a safety net until the new one is
verified end to end). Idempotent — every write is either a same-content
chunk PUT (already-exists is fine) or an entry/JSON-doc PUT that fetches
and takes over `_rev` on a 409, so re-running after the source has moved on
a little (Phase 1 step 5's "catch the gap" re-run) just copies what's new.

Usage:
    python3 migrate-tastings-to-hobby.py [--verify-only]

Reads VAULT_COUCHDB_URL / VAULT_USER / VAULT_COUCHDB_PASSWORD from .env —
admin creds, used for both source and destination (source read-only,
destination bypasses the two members' scoped ACL, which is fine for a
one-time admin-run migration).
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from typing import Any

import httpx

_ENTRY_PREFIX = "tastings/"  # lowercased — LiveSync entry ids are lowercased paths
_NOTE_TYPES = ["whisky", "cigar", "coffee", "pipe", "beer", "raki", "chocolate", "pairing"]


def _env() -> dict[str, str]:
    env: dict[str, str] = dict(os.environ)
    path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env.setdefault(k.strip(), v.strip())
    return env


class DB:
    def __init__(self, base_url: str, db: str, user: str, password: str) -> None:
        self.db = db
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), auth=(user, password), timeout=60.0
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def get(self, doc_id: str) -> dict[str, Any] | None:
        from urllib.parse import quote

        r = await self.client.get(f"/{self.db}/{quote(doc_id, safe='')}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        doc: dict[str, Any] = r.json()
        return doc

    async def put(self, doc_id: str, body: dict[str, Any]) -> str:
        """create / already-fine / take-over-and-overwrite. Returns a verdict."""
        from urllib.parse import quote

        r = await self.client.put(f"/{self.db}/{quote(doc_id, safe='')}", json=body)
        if r.status_code in (201, 202):
            return "written"
        if r.status_code != 409:
            r.raise_for_status()
        existing = await self.get(doc_id)
        if existing is None:
            r2 = await self.client.put(f"/{self.db}/{quote(doc_id, safe='')}", json=body)
            r2.raise_for_status()
            return "written"
        if existing.get("type") == "leaf" and existing.get("data") == body.get("data"):
            return "already-present"
        if existing == {**existing, **{k: v for k, v in body.items() if k != "_rev"}}:
            return "already-present"
        r2 = await self.client.put(
            f"/{self.db}/{quote(doc_id, safe='')}", json={**body, "_rev": existing["_rev"]}
        )
        r2.raise_for_status()
        return "overwritten"


async def main() -> int:
    verify_only = "--verify-only" in sys.argv
    env = _env()
    url = env["VAULT_COUCHDB_URL"]
    user = env["VAULT_USER"]
    pw = env["VAULT_COUCHDB_PASSWORD"]

    src = DB(url, "tastings", user, pw)
    dst = DB(url, "hobby", user, pw)
    try:
        # 1. Entries under Tastings/
        end = _ENTRY_PREFIX[:-1] + chr(ord(_ENTRY_PREFIX[-1]) + 1)
        r = await src.client.get(
            f"/{src.db}/_all_docs",
            params={
                "startkey": f'"{_ENTRY_PREFIX}"',
                "endkey": f'"{end}"',
                "include_docs": "true",
            },
        )
        r.raise_for_status()
        entries = [
            row["doc"]
            for row in r.json()["rows"]
            if not (row.get("doc") or {}).get("deleted")
        ]
        print(f"source entries under Tastings/: {len(entries)}")

        chunk_ids: set[str] = set()
        for e in entries:
            chunk_ids.update(e.get("children") or [])
        print(f"unique chunks referenced: {len(chunk_ids)}")

        if verify_only:
            await _verify(src, dst, entries)
            return 0

        # 2. Copy chunks first (entries reference them; order matters if
        #    anything reads mid-migration).
        counts = {"written": 0, "already-present": 0, "overwritten": 0}
        for cid in sorted(chunk_ids):
            chunk = await src.get(cid)
            if chunk is None:
                print(f"  ! chunk {cid} missing in source, skipping (torn note)")
                continue
            body = {k: v for k, v in chunk.items() if k not in ("_rev",)}
            verdict = await dst.put(cid, body)
            counts[verdict] += 1
        print(f"chunks: {counts}")

        # 3. Copy entry docs.
        counts = {"written": 0, "already-present": 0, "overwritten": 0}
        for e in entries:
            doc_id = str(e["_id"])
            body = {k: v for k, v in e.items() if k not in ("_id", "_rev")}
            verdict = await dst.put(doc_id, body)
            counts[verdict] += 1
        print(f"entries: {counts}")

        # 4. Copy taster's Mango-queryable JSON note docs.
        find = await src.client.post(
            f"/{src.db}/_find",
            json={
                "selector": {"type": {"$in": _NOTE_TYPES}},
                "limit": 10000,
            },
        )
        find.raise_for_status()
        note_docs = find.json()["docs"]
        print(f"source JSON note docs: {len(note_docs)}")

        counts = {"written": 0, "already-present": 0, "overwritten": 0}
        for doc in note_docs:
            doc_id = str(doc["_id"])
            body = {k: v for k, v in doc.items() if k not in ("_id", "_rev")}
            verdict = await dst.put(doc_id, body)
            counts[verdict] += 1
        print(f"JSON note docs: {counts}")

        await _verify(src, dst, entries)
    finally:
        await src.aclose()
        await dst.aclose()
    return 0


async def _verify(src: DB, dst: DB, entries: list[dict[str, Any]]) -> None:
    print("\n--- verifying byte-identical markdown for every entry ---")
    mismatches = 0
    for e in entries:
        src_md = await _reassemble(src, e.get("children") or [])
        dst_entry = await dst.get(str(e["_id"]))
        dst_md = (
            await _reassemble(dst, dst_entry.get("children") or []) if dst_entry else None
        )
        if src_md != dst_md:
            mismatches += 1
            print(f"  MISMATCH: {e.get('path')}")
    if mismatches:
        print(f"{mismatches} mismatches out of {len(entries)} entries")
    else:
        print(f"all {len(entries)} entries byte-identical in hobby")


async def _reassemble(db: DB, children: list[str]) -> str | None:
    parts = []
    for cid in children:
        chunk = await db.get(str(cid))
        if chunk is None:
            return None
        parts.append(str(chunk.get("data") or ""))
    return "".join(parts)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
