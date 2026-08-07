#!/usr/bin/env python3
"""Watch a diffity session for new human comments and thread status changes.

Run this as a single long-lived process under `Monitor` — one process, not a
shell loop calling it repeatedly. The `seen` set only suppresses duplicate
events because it survives across poll iterations in memory; a fresh subprocess
per poll would re-seed every time and never fire an event.

Prints one line per event:

    NEW COMMENT [<id>] <file>:<line> — <body excerpt>
    STATUS CHANGE [<id>] <file>:<line> — open → resolved

Comments already present at startup (including the review's own) are seeded, so
only what arrives after the monitor starts is reported. On a continued session,
pass --seed-file pointing at a small JSON snapshot of previously handled comment
IDs and thread statuses (derived from the session log) so comments posted during
the gap between runs still fire instead of being silently absorbed into a fresh
baseline. Thread and comment IDs must be the full IDs from `agent list --json`,
not the 8-char prefix the `agent comment` CLI prints — matching here is exact
string equality, so a prefix silently matches nothing.

Usage:
    monitor.py                               # fresh baseline, give up after 5 errors
    monitor.py --seed-file seed.json         # continue: seed from prior state, not the live listing
    monitor.py --max-errors 8
"""

import argparse
import json
import subprocess
import sys
import time

BODY_EXCERPT = 120
POLL_SECONDS = 20

# A hung `diffity agent list` can stall a poll for longer than the interval, so
# POLL_SECONDS is the floor between polls, not an exact period.
FETCH_TIMEOUT = 30


def fetch_threads():
    raw = subprocess.check_output(["diffity", "agent", "list", "--json"], timeout=FETCH_TIMEOUT)
    data = json.loads(raw)
    return data if isinstance(data, list) else data.get("threads", [])


def excerpt(body):
    """Collapse whitespace so a multiline body still prints as one event line."""
    return " ".join((body or "").split())[:BODY_EXCERPT]


def is_human(comment):
    # diffity 0.9.5 emits "agent" or "user", but exclude agents rather than
    # matching == "user" so an unfamiliar author type surfaces the comment
    # instead of silently swallowing it.
    #
    # `or {}` rather than a .get default: a key present with an explicit null
    # returns None, which .get's default never covers.
    return (comment.get("author") or {}).get("type", "") != "agent"


def load_seed_file(path):
    with open(path) as f:
        data = json.load(f)
    return set(data.get("comment_ids", [])), dict(data.get("status", {}))


def seed_from_listing(threads):
    """Baseline from a live listing: every human comment already on screen."""
    seen = {
        c["id"] for t in threads for c in (t.get("comments") or []) if c.get("id") and is_human(c)
    }
    status = {t["id"]: t.get("status") for t in threads if t.get("id")}
    return seen, status


def events_for(threads, seen, status):
    """Event lines for one poll. Mutates `seen` and `status` to absorb them.

    Every field is read defensively, with `or <empty>` rather than a .get default
    wherever the value is iterated or indexed — a key present with an explicit
    null returns None, which .get's default never supplies. Its caller also wraps
    this in the poll's try/except, so a shape neither guard anticipated counts
    against --max-errors instead of killing the process outright.
    """
    lines = []
    absorbed = set()
    status_updates = {}
    for thread in threads:
        tid = thread.get("id")
        if not tid:
            # No stable key: it can neither be status-tracked nor deduped, and a
            # None key would collide with every other id-less thread and emit a
            # bogus STATUS CHANGE.
            continue
        file_path = thread.get("filePath", "unknown")
        line = thread.get("startLine", "?")
        new_status = thread.get("status")
        if tid in status and status[tid] != new_status:
            lines.append(
                "STATUS CHANGE [%s] %s:%s — %s → %s"
                % (tid, file_path, line, status[tid], new_status)
            )
        status_updates[tid] = new_status
        for comment in thread.get("comments") or []:
            cid = comment.get("id")
            if not cid or cid in seen or cid in absorbed or not is_human(comment):
                continue
            lines.append(
                "NEW COMMENT [%s] %s:%s — %s"
                % (cid, file_path, line, excerpt(comment.get("body", "")))
            )
            absorbed.add(cid)

    # Commit only after every thread parsed cleanly. A shape that raises partway
    # would otherwise leave comments marked seen whose event lines were never
    # returned — silently dropping them. Deferring makes a poll all-or-nothing,
    # so a failed one is simply re-derived on the next pass.
    seen.update(absorbed)
    status.update(status_updates)
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--seed-file",
        help="JSON file with previously handled comment IDs and thread statuses "
        "(from the session log). When given, only these are seeded, so comments "
        "posted after the last run stopped still fire as events. Omit on a "
        "session's first run to seed fresh from the current listing instead.",
    )
    parser.add_argument(
        "--max-errors",
        type=int,
        default=5,
        help="consecutive poll failures before giving up (default: 5)",
    )
    args = parser.parse_args(argv)

    # Read the seed file before touching the network, so a bad path reports
    # itself rather than being masked by whatever diffity says first.
    seed = None
    if args.seed_file:
        try:
            seed = load_seed_file(args.seed_file)
        except (OSError, ValueError) as err:
            sys.exit("monitor could not start — --seed-file %s: %s" % (args.seed_file, err))

    try:
        initial = fetch_threads()
    except Exception as err:
        sys.exit("monitor could not start — `diffity agent list` failed: %s" % err)

    if seed is not None:
        # The listing above is only a liveness check here; seeding from it would
        # absorb the very comments this session was continued to catch.
        seen, status = seed
        print(
            "monitor armed — continued from %s (%d thread(s), %d comment(s) known),"
            " polling every %gs" % (args.seed_file, len(status), len(seen), POLL_SECONDS),
            flush=True,
        )
    else:
        # Fresh baseline: seed from the current listing so nothing already on
        # screen fires as an event. Only correct for a session's first run —
        # when continuing, this would silently absorb comments posted during the gap.
        seen, status = seed_from_listing(initial)
        print(
            "monitor armed — %d thread(s), %d human comment(s) seeded, polling every %gs"
            % (len(status), len(seen), POLL_SECONDS),
            flush=True,
        )

    errors = 0
    while True:
        try:
            threads = fetch_threads()
            # Inside the same guard as the fetch: a listing shape that slips past
            # events_for's defensive reads would otherwise terminate the process,
            # and a dead poller is indistinguishable from a quiet branch.
            lines = events_for(threads, seen, status)
            errors = 0
        except Exception as err:
            errors += 1
            print("POLL FAILED (%d/%d): %s" % (errors, args.max_errors, err), flush=True)
            if errors >= args.max_errors:
                sys.exit("monitor stopping — diffity agent list keeps failing")
            time.sleep(POLL_SECONDS * errors)  # bounded backoff
            continue

        for line in lines:
            print(line, flush=True)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
