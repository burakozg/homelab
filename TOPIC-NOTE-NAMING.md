# Topic note filenames drift, and orphan the note

**Status: fixed in both writers.** `podcast-digest` pins in a
`control:topic_names` document in its own database (`resolve_note_names` /
`pin_note_names` in `podcast_agent/entities.py`); `security-digest` pins in a
`topic_notes` SQLite table (`src/vault/topics.py:note_name`). Same rule, same
gap-filling semantics, neither reassigns.

Verified against the live vault 2026-08-25: **175 topic notes, 0 split topics, 0
notes whose filename no longer matches `slugify(title)`.** Nothing was damaged
before the fix landed.

Two things remain below: a **timing caveat** for podcast-digest's first run, and
a **residual cross-app gap** that is now half-closed.

## The original bug, for the record

A topic note filed under `slugify(display_name(surfaces))`, recomputed every run.
`display_name` returns the most common surface, ties broken by length — a
*moving* value, because surfaces accumulate:

| Mentions | surfaces | display_name | filename |
| --- | --- | --- | --- |
| 2 | `Fortinet` ×2 | Fortinet | `fortinet.md` |
| 4 | `Fortinet` ×2, `Fortinet Inc.` ×2 | **Fortinet Inc.** | **`fortinet-inc.md`** |

`canonical()` folds `Inc.`/`Ltd`/`Corp`/`GmbH`/`PLC`, a leading article and CVE
spellings, so the aggregation *key* is stable throughout — only the filename
moves. Any pair of surfaces that `canonical()` merges but `slugify()` does not is
a trigger.

Why it mattered more than a stray file: `99 topics/` is section-owned. The old
path keeps every other writer's section and the reader's own prose, orphaned; the
new note has only the renaming app's section; and `[[wikilinks]]` resolve by
filename, so every existing link points at the orphan. Two graph nodes for one
thing — the failure the ownership contract exists to prevent, reached from the
other direction.

## Caveat: podcast-digest's first run under the fix

`pin_note_names` gap-fills from `proposed = slugify(entity.name)`. The pin
document does not exist yet, so **the first run pins whatever the display name
happens to be at that moment** — not necessarily what the existing file is
called.

That is safe *right now*: all 175 notes still have a filename matching
`slugify(title)`, so the first run will pin the names already on disk. The window
closes the moment any display name tips. Either run it soon, or seed
`control:topic_names` from the existing filenames first:

```python
# names = {canonical(title-of-note): filename-stem for each note in 99 topics/}
```

Seeding is the belt-and-braces option and costs one script.

## Residual gap: two private pin stores

Both apps now pin — but into stores **neither can read**: podcast-digest's own
database, and security-digest's SQLite. So for a topic *both apps meet for the
first time from now on*, they can still pin different filenames and split the
note. The original bug, moved up one level.

The only store both writers can see is **the vault itself**.

### What security-digest now does

Before choosing a name it lists the topics folder (one ranged `_all_docs`, ids
only, no chunk reads) and adopts an existing filename when one of the spellings
its corpus has actually used matches:

```python
def _adopt(surfaces, key, existing):
    ordered = sorted(surfaces.items(), key=lambda kv: (-kv[1], kv[0]))
    for candidate in [slugify(s) for s, _ in ordered] + [slugify(key)]:
        if candidate in existing:
            return candidate
    return None
```

The adopted name is then pinned like any other, so it never moves again — and a
failure to reach the vault returns an empty set and falls back to choosing our
own name, never to failing a digest that has already been delivered.

**This half-closes the gap on its own**: whichever writer gets there second
adopts. Only a genuine simultaneous first write can still split.

**The honest limit.** Matching is exact, by filename. If the two corpora share no
spelling of a thing — ours only ever says "Volt", theirs only "Volt Typhoon" —
adoption cannot fire and the topic gets two notes. Closing *that* would need
matching on something looser, which risks merging two unrelated topics onto one
page: worse than the split, and far harder to notice. Left deliberately.

### Suggested for podcast-digest

The same read-before-pin, using the vault it already writes to. `LiveSyncVault`
would need one method; `resolve_note_names` would pass the result into
`pin_note_names` as the proposal for keys it has no pin for. With both sides
doing it, the gap is closed except for the disjoint-spelling case above.

## Suggested for the skill

`~/.claude/skills/obsidian-vault-writer/SKILL.md` section 2 should say:

> **The filename of a shared note is part of the contract.** Derive it once and
> store it; never recompute it from data that changes as the corpus grows. And
> before choosing a new one, look in the vault — it is the only store every
> writer can see, so a name already there is the one to adopt.

Both reference implementations reached for a recomputed name first, and both
then reached for a *private* pin store first. It is worth stating once so the
next writer inherits it.

## How to check whether it has fired

A drifted note is one whose `title:` no longer slugifies to its own filename —
that is the early warning, before the rename. A split is two notes whose titles
share a `canonical()` key. Both were zero on 2026-08-25.
