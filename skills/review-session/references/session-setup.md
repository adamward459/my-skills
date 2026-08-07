# Job: Open and bind the diffity session

**Dispatch:** main-loop only · **Writes:** starts a server, creates `.review-sessions/` · **Returns:** `$BASE`, `$REVIEW_REF`, `$SLUG`, `$LOG`

## Inputs

Two refs, and they are **not** interchangeable. Conflating them is the failure this file exists to
prevent: `<BASE>` identifies the diffity session, `<REVIEW_REF>` identifies the log file.

- `<BASE>` — what to diff *against*; the user's required argument, with no default to fall back on.
  If it arrives empty or still literal, stop and report rather than substituting a trunk name.
  Passed to `--base`, to the `curl` bind, and to `diffity open`.
- `<REVIEW_REF>` — what is *under* review. `diffity --compare` defaults to the working tree, so
  this is always the current branch: `git rev-parse --abbrev-ref HEAD`. Used for **one** thing:
  naming the log — which is what makes a session belong to its branch.
- `<SKILL_DIR>` — the skill directory, from Repo bindings in SKILL.md

## Preconditions

- `which diffity` succeeds. If not, install it: `npm install -g diffity`.
- `git rev-parse --verify <BASE>` succeeds. Report the bad ref and stop; do not try another one.

## Task

1. Start the server, with `<BASE>` as a **branch ref** — never a commit SHA, so the base stays
   meaningful as the branch advances and the session keeps a stable name.

   ```bash
   diffity --base <BASE> --new --no-open      # run in background
   ```

   Note what `--new` does and does not do: it restarts the server, but the session is keyed by
   `(base ref, HEAD hash)`, so on an unchanged HEAD it **reattaches to the existing session with
   its threads intact**. Only a new commit yields an empty session. Phase 4 must read
   `diffity agent list --json` before reposting anything for exactly this reason.

2. Bind the agent CLI to this ref:

   ```bash
   curl -s "http://localhost:5391/diff?ref=<BASE>" -o /dev/null
   ```

   This step is not optional. The `diffity agent` CLI stays bound to whatever ref was last loaded
   through the web endpoint (default `work`) until you load this one. Skip it and every
   `diffity agent comment` silently lands on a different session. `<BASE>` here, not
   `<REVIEW_REF>` — it must match what step 1 passed to `--base`.

3. Verify the binding took:

   ```bash
   diffity agent diff | grep -c '^diff --git'   # must be > 0
   ```

4. Resolve the log path (see `references/thread-log.md` for the format):

   ```bash
   SLUG=$(python3 "<SKILL_DIR>/scripts/slug.py" <REVIEW_REF>)   # what is under review — NOT <BASE>
   mkdir -p .review-sessions
   LOG=".review-sessions/$SLUG.md"
   ```

   `slug.py` slugs whatever ref it is handed and does not second-guess it, so passing `<BASE>`
   here produces a perfectly valid log name that is simply the wrong file — shared by every branch
   reviewed against that base. Getting the argument right is the caller's job.

## Known pitfalls

- **Verify count comes back 0** — almost always the `curl` was skipped or hit a different ref
  than the one passed to `--base`. Re-run it against the exact `<BASE>` and check again before
  posting anything.
- **A `<BASE>` that doesn't resolve looks identical to a skipped `curl`** — both land on a verify
  count of 0. Check the ref exists before re-running the bind.
- **Positional form errors on diffity 0.9.5.** `diffity <branch> --no-open` fails; always use the
  `--base` flag form.
- **A log named after `<BASE>`** is the quiet version of this phase failing. Nothing errors; the
  next review of a different branch just inherits the wrong history. Check `$LOG` before phase 2.

## Prohibitions

- Do NOT delegate this phase to a subagent. Two reasons, both fatal:
  - The server must be owned by the main session so teardown can reach it. A subagent's
    background children are not reliably reachable after it returns.
  - The `curl` step mutates *global* server state — which ref the agent CLI is bound to. Bound
    inside a subagent, that is not observably true for the orchestrator, and every later
    `diffity agent comment` targets the wrong session with no error.
- Do NOT post any comment in this phase.

The phase gate is defined in SKILL.md. If step 3's verify count is 0, **stop and report** rather
than continuing — everything downstream writes into whichever session is actually bound.
