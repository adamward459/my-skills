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
only what arrives after the monitor starts is reported.

Usage:
    monitor.py                        # poll every 20s, give up after 5 errors
    monitor.py --poll 30 --max-errors 8
"""

import argparse
import json
import subprocess
import sys
import time

BODY_EXCERPT = 120


def fetch_threads():
    raw = subprocess.check_output(
        ["diffity", "agent", "list", "--json"], timeout=30
    )
    data = json.loads(raw)
    return data if isinstance(data, list) else data.get("threads", [])


def is_human(comment):
    # The exact non-agent type string varies, so exclude agents rather than
    # matching == "user".
    return comment.get("author", {}).get("type", "") != "agent"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--poll", type=float, default=20.0, help="seconds between polls (default: 20)"
    )
    parser.add_argument(
        "--max-errors",
        type=int,
        default=5,
        help="consecutive poll failures before giving up (default: 5)",
    )
    args = parser.parse_args(argv)

    # Seed from the current listing so nothing already on screen fires as an event.
    try:
        seed = fetch_threads()
    except Exception as err:
        sys.exit("monitor could not start — `diffity agent list` failed: %s" % err)

    seen = {c["id"] for t in seed for c in t.get("comments", []) if is_human(c)}
    status = {t.get("id"): t.get("status") for t in seed}
    print(
        "monitor armed — %d thread(s), %d human comment(s) seeded, polling every %gs"
        % (len(status), len(seen), args.poll),
        flush=True,
    )

    errors = 0
    while True:
        try:
            threads = fetch_threads()
            errors = 0
        except Exception as err:
            errors += 1
            print("POLL FAILED (%d/%d): %s" % (errors, args.max_errors, err), flush=True)
            if errors >= args.max_errors:
                sys.exit("monitor stopping — diffity agent list keeps failing")
            time.sleep(args.poll * errors)  # bounded backoff
            continue

        for thread in threads:
            tid = thread.get("id")
            file_path = thread.get("filePath", "unknown")
            line = thread.get("startLine", "?")
            new_status = thread.get("status")
            if tid in status and status[tid] != new_status:
                print(
                    "STATUS CHANGE [%s] %s:%s — %s → %s"
                    % (tid, file_path, line, status[tid], new_status),
                    flush=True,
                )
            status[tid] = new_status
            for comment in thread.get("comments", []):
                cid = comment["id"]
                if cid not in seen and is_human(comment):
                    body = comment.get("body", "")[:BODY_EXCERPT]
                    print(
                        "NEW COMMENT [%s] %s:%s — %s" % (cid, file_path, line, body),
                        flush=True,
                    )
                    seen.add(cid)

        time.sleep(args.poll)


if __name__ == "__main__":
    sys.exit(main())
