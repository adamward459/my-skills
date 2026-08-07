# Reference: the live thread log

**Kind:** data-format spec · **Read by:** phase 2 (restore), phase 4 (post & record), phase 5 (watch)

This is the single source of truth for the log format. Nothing else restates these fields — if
you need them, read this file.

## Why it exists

Diffity stores comments in a local sqlite database, so they are not lost when the server stops.
What loses them is **committing**: a session is keyed by `(base ref, HEAD hash)`, so the next
commit rolls over to a fresh, empty session and the previous threads stop being listed.

This log is the layer that survives that rollover, so it has to reflect reality after every write,
not just after the initial post.

It is not a substitute for reading the live session. After a restart on an unchanged HEAD the old
threads are still there, and restoring from this log without checking would post all of them a
second time.

## Location

```
.review-sessions/<slug>.md      # one flat file per reviewed ref
```

`<slug>` comes from `scripts/slug.py` — never derive it by hand:

```bash
SLUG="$(python3 "$SKILL_DIR/scripts/slug.py" "$REVIEW_REF")"   # under review — NOT the base
mkdir -p .review-sessions
LOG=".review-sessions/$SLUG.md"
```

The argument is the ref being **reviewed**, not the base being diffed against. `slug.py` slugs
what it is given without judging it, so a base ref produces a valid name for the wrong file — one
shared by every branch reviewed against that base.

The slug is the branch name with `/` → `__` and anything else unsafe → `-`, plus a short digest
of the full branch name so distinct branches can't collide on one file.

`.review-sessions/` is git-ignored. This is local scratch state — never commit it.

## Structure

Two parts, in this order: a machine-readable thread record block, then an append-only exchange log.

### Thread records — JSONL, never a markdown table

One JSON object per line, inside a single fenced ```jsonl block under a `## Threads` heading. One
line per thread, rewritten in place when that thread changes.

````markdown
## Threads

```jsonl
{
  "thread_id": "0020654f-47a8-4c31-9f6a-1d2e3f4a5b6c",
  "severity": "must-fix",
  "file": "src/api/auth.ts",
  "start_line": 41,
  "end_line": 47,
  "side": "new",
  "anchor": "const token = req.headers.authorization\nif (!token) return null",
  "body": "[must-fix] Token is read before the null check.",
  "status": "open",
  "handled_comments": [
    "2f4d0eb2-8af9-479c-ba95-5db750ba8def"
  ]
}
```
````

**JSONL, not a table, because two fields are hostile to tables.** `anchor` is verbatim source, and
`body` is a full comment body — either can contain a `|`, which is every TypeScript union and every
`||`, and a multi-line anchor contains newlines, which a markdown table row cannot represent at all.
Escaping them by hand corrupts the anchor, and the anchor's whole job is being byte-exact. JSON
already escapes both, and one object per line keeps a single thread editable without rewriting the
file.

Write it with a JSON serializer, never by hand-splicing strings — a hand-built line is exactly how
an unescaped quote or newline gets in. Parse it the same way: read the block, `json.loads` each
non-empty line. **A line that fails to parse is a hard error** — report it and stop. Never skip it
and never repair it by guessing, because a dropped line is a finding that silently disappears.

Field rules:

- **`thread_id`** — the **full** ID from `diffity agent list --json`, not the 8-char prefix that
  `diffity agent comment` prints in `Created thread <prefix>`. The monitor compares IDs by exact
  string equality, so a prefix here means its status changes never fire. A rolled-over session
  issues fresh IDs for reposted comments, so this field is rewritten on every restore — it is
  never a stable cross-session key.
- **`severity`** — `must-fix` \| `suggestion` \| `question` \| `nit`.
- **`file`** — repo-relative path.
- **`start_line`** / **`end_line`** — a **range**, as numbers, never a bare line number.
- **`side`** — `new` \| `old`. Which side of the diff the finding attaches to, and what phase 2
  checks the anchor against: the working tree for `new`, `git show "$BASE:<file>"` for `old`.
- **`anchor`** — the **verbatim** source text at that range, copied at post time. Never
  paraphrased, never reflowed, and newlines preserved as `\n` by the serializer. Diffity has its
  own `anchorContent` field, but it is null on most threads — this field is the one that can be
  relied on.
- **`body`** — the full comment body, including its `[severity]` tag.
- **`status`** — `open` \| `resolved` \| `dismissed`.
- **`handled_comments`** — a JSON array of the full IDs of the **human** comments in this thread
  that have already been answered; `[]` on first post. This is what the monitor's seed is built
  from, so it is the one field that must be updated as events are handled rather than only at post
  time. Agent-authored comments are never recorded: the monitor filters them by author anyway.

### Exchange log

Append-only. One line per event: what happened, to which thread, and when. Posts, replies,
resolutions, dismissals, reopens, anchors that went stale, rows reconciled against a live session
rather than reposted, and session start/stop.

This is prose for a human reading back what happened — nothing parses it, so it is the one place
free text is safe. Anything a later phase needs to _act_ on belongs in a thread record field
instead.

## Why range _and_ anchor

A bare `file:line` moves multi-line and old-side findings onto the wrong code when replayed. So
the range is recorded instead.

But a range alone still goes stale the moment the branch advances — the same line numbers now
point at different code. The anchor is the staleness guard: phase 2 compares it against the
file's current content before reposting, so a moved or rewritten hunk gets re-reviewed rather
than silently reattached to unrelated code.

This is why the anchor must be copied verbatim. A paraphrase can never match, which turns every
restore into a false `missing`.

## Status is load-bearing

Closed records (`resolved`, `dismissed`) are **never reposted**. Because a rolled-over session mints
fresh thread IDs, reposting a closed finding creates a brand-new _open_ thread with no memory of
having been settled — silently reopening it and inviting duplicate work.

Closed records stay in the log exactly as they are. They are history, not a work queue.

## Seed snapshot

Phase 5 derives a small JSON file from this log so a continued session's monitor doesn't treat comments
posted since the last run as part of its baseline:

```json
{
  "comment_ids": ["2f4d0eb2-8af9-…"],
  "status": { "0020654f-47a8-…": "open", "8a1c480f-5be6-…": "resolved" }
}
```

- `comment_ids` — the union of every record's **`handled_comments`** array. These are the human
  comments already answered; anything else the monitor sees is new by definition.
- `status` — each record's `thread_id` → its logged `status`, for detecting resolves and reopens.

Written to `$LOG.seed.json` and passed as `monitor.py --seed-file`. Build it **after** phase 4 has
finished writing, so it carries the thread IDs that are actually live rather than the previous
run's. It reflects the log as of that moment — anything that arrived after correctly fires as a new
event on the first poll.

Both maps are matched by exact string equality, so use the full IDs from `agent list --json`
throughout. An 8-char prefix, or a thread ID from before a rollover, matches nothing and every
status change goes unnoticed. JSON object keys are always strings; keep thread IDs strings on both
sides so the comparison doesn't silently miss.
