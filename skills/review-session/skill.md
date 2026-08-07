---
name: review-session
description: Run a continuous, resumable code-review session on the current hayai-learn branch — opens a diffity diff session, restores prior review history from a local git-ignored log, runs an AI code review via the diffity-review skill (or a direct self-review if that's unavailable), posts findings as inline diffity comments, and keeps watching for new comments to address as the branch evolves. Use this whenever the user says "create a review session", "let's review", "review this branch/PR", "run a code review", or "check for new review comments" in this repo — not just when they say the exact phrase "review session". Also use it to resume/continue a review session started earlier, or to stop one.
user-invocable: true
---

# Review Session

A review session is a *standing* process, not a one-shot review: it opens a diffity diff, gets an AI review posted on it, then keeps listening for the user's replies and new comments and keeps addressing them until told to stop. A local git-ignored file is the durable memory layer underneath it, since diffity's own session state is local and ephemeral — restart the diffity server and the comment history is gone unless it was logged.

Follow the steps below in order. Don't skip step 4 (arming the monitor) — it's the step that makes this a *session* rather than a one-off review, and it's the easiest one to forget once findings are posted.

## 1. Open and verify the diffity session

Check that `diffity` is available first: run `which diffity`. If not found, install it with `npm install -g diffity`.

Pin the session to a **branch ref**, not a commit SHA — a SHA-pinned session mints a new session on every commit and loses its comment thread. Use the branch the user names, defaulting to `master`.

```bash
diffity --base <branch> --new --no-open   # run in background
curl -s "http://localhost:5391/diff?ref=<branch>" -o /dev/null   # binds the agent CLI to this ref
diffity agent diff | grep -c '^diff --git'   # verify: must be > 0
```

Why the curl step: the diffity agent CLI stays bound to whatever ref was last loaded through the web endpoint (default `work`) until you load this one. Skipping it means your `diffity agent comment` calls silently land on the wrong session.

**Troubleshooting:** if the grep comes back 0, the most common cause is the curl step being skipped or hitting the wrong ref — re-run the curl against the exact `<branch>` you passed to `--base` and check again before posting anything. Positional `diffity <branch> --no-open` errors on diffity 0.9.5 — always use the `--base` flag form.

## 2. Restore prior history from the local log, if any

Check `.review-sessions/<slug>.md` for an existing review-session note for this branch (a "live thread log"). Don't derive the slug by hand — get it from the helper that ships with this skill, then join it into a path yourself and make sure the directory exists:

```bash
SLUG=$(python3 "$SKILL_DIR/scripts/slug.py" <branch>)   # the exact branch from step 1, not whatever's checked out
mkdir -p .review-sessions
LOG=".review-sessions/$SLUG.md"
```

`$SKILL_DIR` is this skill's own directory (`~/.claude/skills/review-session` on this machine). The slug is the branch name with `/` → `__` and anything else unsafe → `-`, plus a short digest of the full branch name so distinct branches can't collide on one log file.

If one exists, re-post its saved comment threads onto the freshly-opened diffity session with `diffity agent comment`, replaying each row's recorded line range and side (see step 5) so findings land back on the same code, then update the log's thread-ID column with the new IDs this repost mints — a `--new` session always issues fresh thread IDs even for restored comments.

If no note exists yet, you'll create one in step 5. `.review-sessions/` is git-ignored (see `.gitignore`) — this log is local scratch state, not something to commit.

## 3. Run the AI review and post findings

Invoke the **diffity-review** skill against the bound ref rather than hand-rolling a reviewer subagent — it already runs the full analysis playbook (data-flow, state/lifecycle, contract, boundary, edge-case, completeness/test-coverage passes), posts `[must-fix]`/`[suggestion]`/`[question]`-tagged inline comments in severity order, and opens the browser itself. Pass the same ref this session is bound to; use its `focus` argument if the user asked for a targeted review (e.g. "check for security issues").

On a resumed session (step 2 found history), diff new findings against the restored ones and only let it post what's actually new — don't re-raise something already threaded. If the **diffity-review** skill isn't available (not listed among invocable skills), do the review yourself directly: read every changed file, apply the same analysis (data-flow, state/lifecycle, contract, boundary, edge-case, completeness/test-coverage), and post findings the same way — `[must-fix]`/`[suggestion]`/`[question]`-tagged inline comments via `diffity agent comment`, must-fix first.

Open the browser on the ref so the user can follow along: `diffity open <branch>`.

## 4. Arm the monitor

Start a persistent `Monitor` that polls `diffity agent list --json` roughly every 20s and emits one event per new *human* comment or thread status change. Track per-thread status as well as comment IDs — a resolve or dismiss leaves no comment behind. Seed both from one listing fetched before the loop starts, so pre-existing and the review's own comments don't self-trigger.

Filter to non-agent authors by checking `author.type != "agent"` rather than matching `== "user"` — the exact type string varies. Compare by comment ID, not content.

Give the poll a timeout and print failures instead of swallowing them, with a bounded backoff and an exit after repeated errors — a silently stalled monitor is indistinguishable from one with nothing to report.

Don't hand-roll the poller — run the one that ships with this skill:

```bash
python3 "$SKILL_DIR/scripts/monitor.py"                    # 20s poll, gives up after 5 errors
python3 "$SKILL_DIR/scripts/monitor.py" --poll 30 --max-errors 8
```

It seeds from one listing at startup, then prints `NEW COMMENT [<id>] <file>:<line> — <body>` and `STATUS CHANGE [<id>] ... open → resolved` lines as events arrive. It prints an `armed` line with the seeded counts on startup — if that line never appears, the monitor isn't running.

**Critical implementation detail** (why it's a script and not an inline loop): the monitor must be a **single long-running process** that persists the `seen` set in memory across poll iterations. Do NOT wrap it in a shell `while` loop that re-invokes it each iteration — the seen set resets on every invocation and no events ever fire. Start it once under `Monitor` and leave it running.

When an event fires, resolve it directly with the `diffity agent` CLI the CLI already does all the work:

1. **Skip** general comments (`filePath` is `__general__`) — these are summaries, not actionable code changes.
2. **Skip** threads where the last comment is an agent reply awaiting the user's response (e.g. "Could you clarify...?") and the user hasn't answered yet.
3. **`[nit]` comments** are still actionable — resolve them like any other finding.
4. **`[question]` comments** — read the question, check the relevant code, and answer via `diffity agent resolve <id> --summary "<answer>"`.
5. Otherwise, read the comment body and the surrounding source, make the requested change with the Edit tool, then resolve it: `diffity agent resolve <id> --summary "Fixed: <what changed>"`.
6. If the comment is genuinely unclear, reply instead of guessing: `diffity agent reply <id> --body "Could you clarify...?"`.

## 5. Keep the local log current, and keep going

Every time you post, reply, resolve, or dismiss a comment, update the live thread log at `$LOG` from step 2 — a table of `thread ID | severity | file | start-end lines | side | full comment body | status`, plus an append-only exchange log of what happened and when. This is what step 2 restores from on the next session, so it needs to reflect reality, not just the initial post.

Record the line *range* and the diff side (`new`/`old`), not a bare `file:line` — step 2 replays these rows verbatim, and a single line number moves multi-line and old-side findings onto the wrong code.

The review session doesn't end after the first pass — keep monitoring, addressing feedback, and re-reviewing as the branch evolves, until the user says to stop.

## Stopping a session

When the user says to stop the review (or "stop the monitor"), tear down everything this session spawned: `TaskStop` the Monitor, `diffity kill` the server, and stop any other background tasks the session started. This is safe because the local live thread log is the durable record — the session is fully restorable via steps 1–2 next time. Before tearing down, make sure the thread log reflects final state and add a line to the exchange log noting the session stopped.

## Repo bindings

- Base branch default: `master`
- Local log dir: `.review-sessions/` (git-ignored — see `.gitignore`)
- Diffity port: `5391`
- Helper scripts (next to this file): `scripts/slug.py` (branch → log slug), `scripts/monitor.py` (comment poller)
