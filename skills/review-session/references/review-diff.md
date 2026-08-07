# Job: Review the diff and report findings

**Dispatch:** subagent · **Writes:** nothing · **Returns:** JSON

Read the changed surface and report what's wrong. This is the largest context consumer in the
session, which is why it is delegated — and it **returns** findings rather than posting them, so
the orchestrator can dedupe against already-threaded work before anything reaches diffity.

## Inputs

The dispatcher substitutes these. If any is still a literal `<PLACEHOLDER>`, stop and return the
error shape below.

- `<BASE>` — the base ref the diffity session is bound to. The code under review is what
  `diffity agent diff` returns against it; you do not need to resolve it yourself.
- `<ALREADY_THREADED>` — findings already open in this session; do not raise these again. `none`
  on a fresh session.

## Preconditions

Verify before doing any work. On failure, return the error shape and stop.

- `diffity agent diff` exits 0 and prints at least one `diff --git` line. If it prints none, the
  agent CLI is bound to the wrong ref — return an error rather than reviewing an empty diff.

## Task

1. Get the diff with `diffity agent diff`. Read the full current contents of the changed files
   too — a diff hunk alone hides the context a finding depends on.
2. Run the full playbook. Each pass is a distinct lens; don't collapse them:
   - **Data-flow** — where values come from, where they're trusted, where they're lost
   - **State/lifecycle** — initialization, ordering, cleanup, re-entrancy
   - **Contract** — call sites vs. signatures, error and null contracts, API compatibility
   - **Boundary** — inputs at the edges, parsing, encoding, permissions
   - **Edge-case** — empty, zero, one, huge, concurrent, failure paths
   - **Completeness / test-coverage** — what changed without a test, what a test asserts vs.
     what it claims to
   Every pass runs on every review, evenly. There is no emphasis input to weight them by, so a
   pass is never traded away for another.
3. Drop anything already in `<ALREADY_THREADED>` — same location or same underlying issue worded
   differently.
4. For each surviving finding, copy the **verbatim** source text at its range as the `anchor`.

## Return

Emit JSON and nothing else — no prose before or after, no markdown fence.

```json
{
  "findings": [
    {
      "severity": "must-fix",
      "file": "src/api/auth.ts",
      "side": "new",
      "start_line": 41,
      "end_line": 47,
      "anchor": "const token = req.headers.authorization",
      "body": "[must-fix] Token is read before the null check, so an unauthenticated request throws instead of returning 401. Move the guard above line 41."
    }
  ]
}
```

Field rules:

- `severity` — `must-fix` \| `suggestion` \| `question` \| `nit`.
- `side` — `new` \| `old`. Which side of the diff the finding attaches to.
- `start_line` / `end_line` — a **range**, even when it is one line (`41`-`41`).
- `anchor` — **required, verbatim, non-empty.** Copy the real source text at that range; never
  paraphrase or reconstruct it. A paraphrased anchor can never match on a later restore, which
  silently turns the finding into an unresolvable one.
- `body` — starts with the `[severity]` tag. Say what's wrong, why it matters, and the concrete
  fix. One finding per entry.
- Return `"findings": []` if the diff is clean. That is a valid result, not an error.

## Prohibitions

- Do NOT run `diffity agent comment`, `reply`, `resolve`, or `dismiss`. Posting is the
  orchestrator's job and posting here makes dedupe impossible.
- Do NOT create, edit, or delete any file. Report the fix; do not apply it.
- Do NOT read or write anything under `.review-sessions/`.
- Do NOT start background processes or dispatch further subagents.

## On failure

Return `{"error": "<one sentence>"}`. Never invent findings to fill a quiet review — an empty
`findings` array is a legitimate answer.
