# Job: Arm the monitor and route its events

**Dispatch:** main-loop only · **Writes:** the seed snapshot, then the log as events are handled · **Returns:** a live monitor

This is what makes the run a *session* rather than a one-off review.

## Inputs

- `<SKILL_DIR>` — absolute path to the skill directory
- `<LOG>` — the session log path from phase 1
- `<MODE>` — `fresh` (no prior log) or `continued` (phase 2 restored history). Derived by the
  orchestrator from whether `<LOG>` existed, never supplied by the user. It selects the seeding
  path below and nothing else.

## Task

Run the poller that ships with this skill. Do not hand-roll one.

```bash
python3 "<SKILL_DIR>/scripts/monitor.py"                              # fresh session
python3 "<SKILL_DIR>/scripts/monitor.py" --seed-file "<LOG>.seed.json"  # continued session
python3 "<SKILL_DIR>/scripts/monitor.py" --max-errors 8
```

It polls `diffity agent list --json` every 20s — a fixed interval, not a flag, and a floor rather
than an exact period since a slow listing can stretch it — and emits one event per new *human*
comment or per thread status change. Status is tracked alongside comment IDs because a resolve or
dismiss leaves no comment behind.

Start it **once**, under `Monitor`, and leave it running.

## Seeding

The seed decides what counts as "already seen", and getting it wrong loses comments silently.

- **Fresh** — no `--seed-file`. The script seeds from one listing fetched before the loop starts,
  so pre-existing comments and the review's own don't self-trigger.
- **Continued** — seeding from the current listing would absorb the very comments this session was
  continued in order to answer. That is a real risk, not a theoretical one: a restart on an
  unchanged HEAD reattaches to the *same* diffity session, so every human comment from the last
  run is still listed and would be written off as baseline. Instead build `<LOG>.seed.json` from
  what the log records as already handled and pass it as `--seed-file`. See
  `references/thread-log.md` for the shape. Anything not in that snapshot then correctly fires on
  the first poll.

Use the full IDs from `agent list --json` on both sides. The comparison is exact string equality,
so an 8-char thread prefix in the seed means no status change ever fires — a failure that looks
exactly like a quiet branch.

## Events

```
NEW COMMENT [<id>] <file>:<line> — <body excerpt>
STATUS CHANGE [<id>] <file>:<line> — open → resolved
```

On startup it prints an `armed` line with the seeded counts. **If that line never appears, the
monitor is not running** — treat its absence as a failed gate, not as a quiet session.

## Routing

Two event kinds, handled differently. Resolve each with the `diffity agent` CLI.

### STATUS CHANGE — a resolve, dismiss, or reopen, with no new body

1. Update the log's status column for that thread. Touch no code.
2. If the transition is a **reopen** (back to `open` from `resolved`/`dismissed`), treat it as a
   signal the finding needs a fresh look — re-review the associated code rather than treating it
   as a no-op.

### NEW COMMENT

1. **Skip** general comments (`filePath` is `__general__`) — summaries, not actionable changes.
2. **Skip** threads whose last comment is an agent reply awaiting the user (e.g. "Could you
   clarify…?") that they haven't answered yet.
3. **`[nit]`** comments are still actionable — resolve them like any other finding.
4. **`[question]`** comments — read the question, check the code, answer via
   `diffity agent resolve <id> --summary "<answer>"`.
5. Otherwise read the body and surrounding source, make the change, then
   `diffity agent resolve <id> --summary "Fixed: <what changed>"`.
6. If genuinely unclear, reply instead of guessing:
   `diffity agent reply <id> --body "Could you clarify…?"`.

Mind the flag asymmetry: `resolve` and `reply` take `--summary` and `--body`, but `dismiss` takes
`--reason`. `dismiss --summary` errors out.

Every one of these is a log write, and it must include adding the comment's ID to that thread's
**handled comments** column — otherwise the next run re-answers it. Route back through phase 4
rather than editing the log ad hoc.

## Prohibitions

- Do NOT delegate this phase to a subagent. The `Monitor` tool is main-session only, and
  `monitor.py` must be a single long-lived process whose in-memory `seen` set survives across
  polls. A subagent terminates when it returns, taking the poller with it.
- Do NOT wrap the script in a shell `while` loop that re-invokes it each iteration. The seen set
  resets on every invocation and no event ever fires — the failure looks exactly like a quiet
  branch.
- Do NOT swallow poll failures. They print, back off, and exit after `--max-errors`; a silently
  stalled monitor is indistinguishable from one with nothing to report.

The phase gate is defined in SKILL.md. Absence of the `armed` line means the session is not
watching anything — treat it as a failure, not as a quiet branch.
