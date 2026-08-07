# Job: Restore prior review history from the session log

**Dispatch:** subagent · **Writes:** nothing · **Returns:** JSON

Read the whole log and check every anchor against current source, then hand back a short
classification. This is delegated because it reads one log file plus N source files to produce a
bounded list — exactly the shape that should not sit in the orchestrator's context.

## Inputs

The dispatcher substitutes these. If any is still a literal `<PLACEHOLDER>`, stop and return the
error shape below.

- `<LOG_PATH>` — absolute path to the session log to restore from
- `<THREAD_LOG_SPEC>` — absolute path to `references/thread-log.md`, which defines the table you
  are parsing
- `<REPO_ROOT>` — absolute path the log's `file` column is relative to. Anchors are checked against
  the **working tree** here, which is what is under review; you do not need to resolve any ref.

## Preconditions

Verify before doing any work. On failure, return the error shape and stop.

- `<LOG_PATH>` exists and is readable.
- `<THREAD_LOG_SPEC>` exists and is readable.

## Task

1. Read `<THREAD_LOG_SPEC>` first. It defines the thread table's columns and their meaning.
2. Read `<LOG_PATH>` and parse its thread table.
3. **Filter to active rows only** — status is neither `resolved` nor `dismissed`. Count what you
   skipped; do not include closed rows in your output.
4. For each active row, open `<REPO_ROOT>/<file>` and locate the recorded `anchor` text:
   - Present at the recorded `start-end` range → `matches`. Return the recorded range.
   - Present elsewhere in the file → `moved`. Return its **current** range.
   - Not present anywhere in the file, or the file no longer exists → `missing`. Return the
     recorded range unchanged.
5. Compare the anchor as **exact text**. Do not fuzzy-match, normalize whitespace, or accept a
   near-match — a false `matches` reattaches a finding to unrelated code, which is worse than a
   false `missing`.

## Return

Emit JSON and nothing else — no prose before or after, no markdown fence.

```json
{
  "rows": [
    {
      "old_thread_id": "a1b2c3",
      "severity": "must-fix",
      "file": "src/api/auth.ts",
      "side": "new",
      "start_line": 41,
      "end_line": 47,
      "anchor": "const token = req.headers.authorization",
      "body": "[must-fix] Token is read before the null check.",
      "handled_comments": ["2f4d0eb2-8af9-479c-ba95-5db750ba8def"],
      "anchor_state": "matches"
    }
  ],
  "skipped_closed": 7
}
```

Field rules:

- `anchor_state` — one of `matches`, `moved`, `missing`. Required on every row.
- `start_line` / `end_line` — for `moved`, the **current** location; otherwise as recorded.
- `body` — the full original comment body, verbatim. The orchestrator reposts this text.
- `old_thread_id` / `handled_comments` — copied through verbatim from the row. They still identify
  live threads and comments when the session was merely restarted, so the orchestrator needs them
  to tell a reconcile from a repost. Return `[]` for an empty handled-comments cell.
- `skipped_closed` — how many `resolved`/`dismissed` rows you excluded.
- Return `"rows": []` if the log has no active rows. That is a valid result, not an error.

## Prohibitions

- Do NOT run `diffity agent comment`, `reply`, `resolve`, or `dismiss` — or any `diffity`
  subcommand at all.
- Do NOT create, edit, or delete any file, including the log.
- Do NOT repost or otherwise act on what you find. You classify; the orchestrator writes.
- Do NOT start background processes or dispatch further subagents.

## On failure

Return `{"error": "<one sentence>"}`. Never return partial rows or a guessed classification —
an unreliable `matches` is worse than an honest failure.
