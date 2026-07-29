---
name: review-session
description: Run a continuous, resumable code-review session on the current hayai-learn branch — opens a diffity diff session, restores prior review history from a local git-ignored log, dispatches an AI code review via superpowers:requesting-code-review, posts findings as inline diffity comments, and keeps watching for new comments to address as the branch evolves. Use this whenever the user says "create a review session", "let's review", "review this branch/PR", "run a code review", or "check for new review comments" in this repo — not just when they say the exact phrase "review session". Also use it to resume/continue a review session started earlier, or to stop one.
user-invocable: true
---

# Review Session

A review session is a *standing* process, not a one-shot review: it opens a diffity diff, gets an AI review posted on it, then keeps listening for the user's replies and new comments and keeps addressing them until told to stop. A local git-ignored file is the durable memory layer underneath it, since diffity's own session state is local and ephemeral — restart the diffity server and the comment history is gone unless it was logged.

Follow the steps below in order. Don't skip step 4 (arming the monitor) — it's the step that makes this a *session* rather than a one-off review, and it's the easiest one to forget once findings are posted.

## 1. Open and verify the diffity session

Check that `diffity` is available first: run `which diffity` (same check the **diffity-diff** and **diffity-resolve** skills use). If not found, install it with `npm install -g diffity`.

Pin the session to a **branch ref**, not a commit SHA — a SHA-pinned session mints a new session on every commit and loses its comment thread. Use the branch the user names, defaulting to `master`.

This session needs to bind the agent CLI to a specific base-branch comparison (`--base`/`--new`) rather than just opening the working-tree diff, so it doesn't delegate to the **diffity-diff** skill's open step — that skill covers the simpler "just show me the diff" case, not ref-pinned agent-bound sessions.

```bash
diffity --base <branch> --new --no-open   # run in background
curl -s "http://localhost:5391/diff?ref=<branch>" -o /dev/null   # binds the agent CLI to this ref
diffity agent diff | grep -c '^diff --git'   # verify: must be > 0
```

Why the curl step: the diffity agent CLI stays bound to whatever ref was last loaded through the web endpoint (default `work`) until you load this one. Skipping it means your `diffity agent comment` calls silently land on the wrong session.

**Troubleshooting:** if the grep comes back 0, the most common cause is the curl step being skipped or hitting the wrong ref — re-run the curl against the exact `<branch>` you passed to `--base` and check again before posting anything. Positional `diffity <branch> --no-open` errors on diffity 0.9.5 — always use the `--base` flag form.

## 2. Restore prior history from the local log, if any

Check `.review-sessions/<branch>.md` for an existing review-session note for this branch (a "live thread log"). If one exists, re-post its saved comment threads onto the freshly-opened diffity session with `diffity agent comment`, then update the log's thread-ID column with the new IDs this repost mints — a `--new` session always issues fresh thread IDs even for restored comments.

If no note exists yet, you'll create one in step 5. `.review-sessions/` is git-ignored (see `.gitignore`) — this log is local scratch state, not something to commit.

## 3. Run the AI review and post findings

Dispatch the review via the **superpowers:requesting-code-review** skill (not a hand-rolled review) — split large diffs by area (e.g. backend/frontend) so each reviewer subagent has focused context, and validate each finding against the full surrounding code before trusting it.

Post findings as inline comments with `diffity agent comment`, prefixed `[must-fix]`, `[suggestion]`, or `[question]`, must-fix findings first. On a resumed session (step 2 found history), diff new findings against the restored ones and only post what's actually new — don't re-raise something already threaded.

Open the browser on the ref so the user can follow along: `diffity open <branch>`.

## 4. Arm the monitor

Start a persistent `Monitor` that polls `diffity agent list --json` roughly every 20s and emits one event per new *human* comment or status change. Seed its "seen" set with every comment ID present at startup so pre-existing and the review's own comments don't self-trigger.

Filter to non-agent authors by checking `author.type != "agent"` rather than matching `== "user"` — the exact type string varies. Compare by comment ID, not content.

**Critical implementation detail:** The monitor must use a **single long-running process** that persists the `seen` set in memory across poll iterations. Do NOT use a shell `while` loop that spawns a fresh subprocess each iteration — the seen set resets on every invocation and no events ever fire. Use a single python (or node) script with `import time; while True: ... time.sleep(20)` so the set grows in-place.

Example pattern:
```python
#!/usr/bin/env python3
import json, subprocess, sys, time

SEEN = set(sys.argv[1].split(',')) if len(sys.argv) > 1 and sys.argv[1] else set()
while True:
    try:
        raw = subprocess.check_output(['diffity', 'agent', 'list', '--json'], stderr=subprocess.DEVNULL)
        data = json.loads(raw)
        threads = data if isinstance(data, list) else data.get('threads', [])
        for thread in threads:
            for comment in thread.get('comments', []):
                cid = comment['id']
                author = comment.get('author', {})
                if cid not in SEEN and author.get('type', '') != 'agent':
                    file_path = thread.get('filePath', 'unknown')
                    line = thread.get('startLine', '?')
                    body = comment.get('body', '')[:120]
                    print(f'NEW COMMENT [{cid}] {file_path}:{line} — {body}', flush=True)
                    SEEN.add(cid)
    except Exception:
        pass
    time.sleep(20)
```

When an event fires, invoke the **diffity-resolve** skill (`Skill({ skill: "diffity-resolve" })`) to reply and make the code fix inline where the comment is actionable — don't re-derive its actionability rules (skip general comments, skip agent-waiting-for-user threads, handle `[nit]`/`[question]` tags) here, that logic lives in diffity-resolve and would drift if duplicated.

## 5. Keep the local log current, and keep going

Every time you post, reply, resolve, or dismiss a comment, update `.review-sessions/<branch>.md`'s live thread log — a table of `thread ID | severity | file:line | finding | status`, plus an append-only exchange log of what happened and when. This is what step 2 restores from on the next session, so it needs to reflect reality, not just the initial post.

The review session doesn't end after the first pass — keep monitoring, addressing feedback, and re-reviewing as the branch evolves, until the user says to stop.

## Stopping a session

When the user says to stop the review (or "stop the monitor"), tear down everything this session spawned: `TaskStop` the Monitor, `diffity kill` the server, and stop any other background tasks the session started. This is safe because the local live thread log is the durable record — the session is fully restorable via steps 1–2 next time. Before tearing down, make sure the thread log reflects final state and add a line to the exchange log noting the session stopped.

## Repo bindings

- Base branch default: `master`
- Local log dir: `.review-sessions/` (git-ignored — see `.gitignore`)
- Diffity port: `5391`
