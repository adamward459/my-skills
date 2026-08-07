---
name: review-session
description: Run a standing code-review session on the current branch — opens a diffity diff, posts findings as inline comments, and watches for new comments. Continues the current branch's session automatically when one exists, otherwise starts fresh. Takes one required argument, the base ref to diff against, and nothing else.
argument-hint: "<base-ref>"
disable-model-invocation: true
---

# Review Session

A *standing* review process, not a one-shot review. It opens a diffity diff, posts a review onto
it, then keeps listening for the user's replies and new comments and keeps addressing them until
told to stop. A one-shot review — nothing left running, no log file, no server — is the
**diffity-review** skill instead.

Diffity does persist comments — in a local sqlite store, not in memory. But a session is keyed by
**(base ref, HEAD hash)**, so **committing anything rolls the session over** to an empty one and
the old threads stop being listed. Restarting the server does *not*: on an unchanged HEAD even
`--new` reattaches to the existing session with every thread intact.

Both halves are load-bearing. The git-ignored log is what survives the rollover, so it is what a
continued session restores from. The live listing is what stops that restore from reposting threads
that never went away.

## Invocation

`/review-session` takes **one required argument: the base ref.** There are no modes and no other
arguments.

- `$BASE` — what to diff *against*. The only thing the user may pass, and it has **no default**. On
  an empty brief, ask for it and stop until answered. Never fall back to a trunk name and never
  infer one from git: `origin/HEAD`, `@{u}`, and merge-base are defaults in disguise, and they fail
  the same silent way — the session binds to the wrong ref, and every finding, thread, and log row
  is built on the wrong diff with nothing erroring.
- `$REVIEW_REF` — what is *under* review: always the current branch
  (`git rev-parse --abbrev-ref HEAD`). Not user-settable. It names the log file, so it must never
  be `$BASE`.

**Continue automatically.** A session belongs to the branch it reviews, and the log named after
that branch is the whole of the state. If `$LOG` exists, this run continues that session —
silently. Do not announce it, do not ask whether to continue, do not offer to start over. If `$LOG`
does not exist, this is a fresh session. The phases below detect which without any input from the
user.

A missing `$BASE` is the **only** thing this skill ever asks about, and it asks once — phase 5
re-enters phase 1 on every new commit, and that re-entry reuses the base already bound.

## Invariants

These hold across every phase.

1. **The main session is the sole writer** to diffity and to `$LOG`. Subagents return data; the
   orchestrator posts and records. You cannot dedupe against something an agent already posted.
2. **Pin `$BASE` to a branch ref, never a commit SHA** — so the base stays meaningful as the
   branch advances, and `diffity open` and the log slug stay stable. This does **not** keep the
   session across commits: the session key includes the HEAD hash, so a branch-pinned session
   rolls over on every commit exactly like a SHA-pinned one. Phase 2 is what covers that.
3. **Every finding carries a verbatim anchor** — the real source text at its range, copied not
   paraphrased. It is what survives the branch moving on. Diffity has its own `anchorContent`
   field; do not rely on it, it is null on most threads.
4. **Closed threads are never reposted.** Fresh IDs mean a reposted `resolved` finding returns as
   a new *open* thread with no memory of being settled.
5. **The monitor is one long-lived process.** Re-invoking it per poll resets its seen set and no
   event ever fires.
6. **Reconcile against the live session before reposting.** A continued session on an unchanged
   HEAD finds its old threads still listed; reposting from the log without checking duplicates
   every one.
7. **Log the full IDs from `agent list --json`**, never the 8-char prefix `agent comment` prints.
   The monitor compares IDs by exact string equality, so a prefix matches nothing.

## Phases

The run is a phase machine. Each phase ends with a **gate** that must hold before the next
begins. A failed gate **stops the run and reports which gate failed** — never proceed hoping a
later phase compensates.

| # | Phase | Runs in | Loads | Produces |
|---|---|---|---|---|
| 1 | Setup | main | `references/session-setup.md` | bound `$BASE`, `$REVIEW_REF`, `$SLUG`, `$LOG` |
| 2 | Restore | **subagent** | `references/restore-history.md` | rows JSON |
| 3 | Review | **subagent** | `references/review-diff.md` | findings JSON |
| 4 | Post & record | main | `references/thread-log.md` | threads posted, log written |
| 5 | Watch | main | `references/monitor-alerts.md` | monitor armed, events routed |

---

### Phase 1 — Setup  `[main]`

**Goal.** A diffity session pinned to `$BASE`, with the agent CLI bound to it, and `$LOG` named
after `$REVIEW_REF`.

**Load.** `$SKILL_DIR/references/session-setup.md` and follow it.

**Gate.** `$BASE` is non-empty, `diffity agent diff | grep -c '^diff --git'` > 0, and `$LOG` is
resolved **and derived from `$REVIEW_REF`, not `$BASE`**. Check `$BASE` first, before the server
starts: blank, it reaches `diffity --base` as an empty string and binds the session to nothing. If
the count is 0 the CLI is bound to the wrong ref — stop. Everything downstream would write into
whichever session is actually bound. If the log is named after `$BASE`, every branch reviewed
against that base shares one log and phase 2 will restore another branch's findings.

---

### Phase 2 — Restore  `[subagent]`

**Goal.** Recover still-open findings from a prior session, and know which anchors went stale.

`$LOG`'s existence is the auto-continue detector — the only one. If `$LOG` does not exist, this is
a fresh session: skip to phase 3. If it exists, continue that session by running this phase. Either
way, decide it from the filesystem and say nothing about it to the user.

**Dispatch** one agent:

> Read `<SKILL_DIR>/references/restore-history.md` and execute it as your complete instruction
> set. Inputs: `LOG_PATH=<abs>`, `THREAD_LOG_SPEC=<abs>`,
> `REPO_ROOT=<abs>`. Follow its Prohibitions exactly. Return only the JSON its Return section
> specifies.

Every `<abs>` and `<SKILL_DIR>` above is a substitution site, not literal text — expand each to a
full absolute path before dispatching. A subagent inherits no shell state, so an unexpanded
`$SKILL_DIR` reaches it as the characters `$SKILL_DIR` and the read fails.

Carry its `rows` into phase 3 as `ALREADY_THREADED`, and into phase 4 for reposting.

**Gate.** Valid JSON, every row carrying an `anchor_state`. On `{"error": …}`, stop and report.

---

### Phase 3 — Review  `[subagent]`

**Goal.** Findings on the current diff, excluding anything already threaded.

**Dispatch** one agent — or several in parallel split by analysis dimension or file group when
the diff is large, then merge and dedupe:

> Read `<SKILL_DIR>/references/review-diff.md` and execute it as your complete instruction set.
> Inputs: `BASE=…`, `ALREADY_THREADED=<compact list from phase 2>`.
> Follow its Prohibitions exactly. Return only the JSON its Return section specifies.

`<SKILL_DIR>` is a substitution site here too — expand it to the absolute path before dispatching.

When phase 2 was skipped, pass `ALREADY_THREADED=none` — the module treats a leftover literal
`<ALREADY_THREADED>` as a dispatch error and refuses to review.

**Gate.** Valid JSON against the module's schema, and every finding carries a non-empty verbatim
`anchor`. An empty `findings` array is a valid result. On `{"error": …}`, stop and report.

---

### Phase 4 — Post & record  `[main]`

**Goal.** Everything reaches diffity and the log, exactly once.

**Load** `$SKILL_DIR/references/thread-log.md` for the log format.

0. **Read the live session first**: `diffity agent list --json`. This is the only way to tell a
   rolled-over session (threads gone, reposting is correct) from a restarted one on an unchanged
   HEAD (threads still there, reposting duplicates them). Do it once and reuse the result.
1. **Reconcile restored rows** (phase 2) against that listing. A row whose thread is still live
   needs **no repost** — only its log row refreshed from the live status. Repost only rows with no
   live counterpart: `matches` and `moved` at their returned range and side. Re-review `missing`
   rows against the current diff instead of reposting at stale coordinates. Never repost a closed
   row.
2. **Post new findings** (phase 3) with `diffity agent comment`, **must-fix first**, each body
   tagged `[must-fix]`/`[suggestion]`/`[question]`/`[nit]`. Drop any that duplicates a live or
   restored thread — same location, or the same issue worded differently.
3. **Record every write** in `$LOG`: thread ID, severity, file, range, side, anchor, body, status,
   handled comment IDs — plus an exchange-log line for what happened and why (including rows whose
   anchor went stale, and rows that were reconciled rather than reposted). Take thread IDs from
   `agent list --json`, **not** from the `Created thread <prefix>` line `agent comment` prints —
   that is an 8-char prefix, and the monitor matches IDs exactly.
4. Open the browser: `diffity open $BASE` — the session is identified by its base ref.

**Gate.** Every live thread has exactly one log row, carrying its full ID and a non-empty anchor,
and **no two live threads share the same file, range, side, and body**. Diffity keys threads by
unique ID, so a literal duplicate ID is impossible — the failure invariant 6 warns about surfaces
as two *distinct* threads carrying the same finding at the same place.

---

### Phase 5 — Watch  `[main]`

**Goal.** Keep the session alive and answer what arrives.

**Load.** `$SKILL_DIR/references/monitor-alerts.md` and follow it, including its routing rules
for `NEW COMMENT` vs `STATUS CHANGE` events.

Its `<MODE>` input is **derived**, never asked, from one filesystem test: `continued` if `$LOG`
existed at phase 2, `fresh` if it did not. How many rows phase 2 returned is irrelevant — a log
whose rows are all closed still means `continued`. It selects how the monitor is seeded, and
getting it wrong loses comments silently.

**Gate.** The monitor printed its `armed` line. If it never appeared, the monitor is not running
— stop and report rather than treating a quiet branch as good news.

**This phase does not terminate.** Each event handled routes back through phase 4 to record it.
A branch that has **advanced re-enters phase 1** — a new commit changes the session key, so the
old threads are gone from diffity and only the log still has them; phases 1 and 2 are what rebind
the agent CLI and repost the survivors. Re-entering at phase 3 would lose every prior finding.
The session keeps going until the user stops it.

## Stopping a session

There is no stop *command*. A plain "stop the review" or "stop the monitor" is the whole interface,
and it is enough. Never leave a session running because the stop wasn't phrased as a command.

Tear down everything the session spawned: `TaskStop` the Monitor, `diffity kill` the server, and
stop any other background tasks it started. This is safe because the log is the durable record —
the session is fully restorable via phases 1–2 next time.

Before tearing down, make sure the log reflects final state — including the **handled comments**
column, since that is what the next run seeds the monitor from — and add an exchange-log line
noting the session stopped.

## Deployment

This repo is the source of truth. The installed skill at `~/.claude/skills/review-session` should
be a **symlink to this repo checkout**, not a copy — a copy silently goes stale, and a multi-file
skill half-deploys in ways that look like model error rather than a missing file. If a
`references/` or `scripts/` file fails to read, suspect the deployment before the instructions.

```bash
ln -s "$(git -C <this-repo> rev-parse --show-toplevel)/skills/review-session" \
  ~/.claude/skills/review-session
```

## Repo bindings

- `$SKILL_DIR`: `~/.claude/skills/review-session` — spell it out in full in every subagent
  dispatch. Subagents inherit no shell state, so a bare `$SKILL_DIR` in a prompt is just text.
- `$BASE`: **no default**, deliberately — see [Invocation](#invocation). A trunk name added here is
  the whole bug, so leave it out.
- `$REVIEW_REF` default: the current branch (`git rev-parse --abbrev-ref HEAD`). This is the **only**
  place that default is written down.
- Local log dir: `.review-sessions/` (git-ignored)
- Diffity port: `5391`
- Helper scripts: `scripts/slug.py` (branch → log slug), `scripts/monitor.py` (comment poller)
