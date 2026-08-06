#!/usr/bin/env python3
"""Normalize a git branch name into a `.review-sessions/` log slug.

The live thread log lives at `.review-sessions/<slug>.md`, one flat file per
branch. A branch name can contain `/` and other characters that are illegal or
awkward in a filename, so it gets normalized here rather than by hand:

    feature/payments        -> feature__payments
    feat/JIRA-12 fix cache  -> feat__JIRA-12-fix-cache
    release/v1.2.3          -> release__v1.2.3

Case is preserved deliberately: slugs minted before this script existed used a
bare `/` -> `__` swap, and lowercasing would orphan their log files.

Usage:
    slug.py                      # slug for the current branch
    slug.py feature/payments     # slug for a named branch
    slug.py --path               # .review-sessions/<slug>.md for the current branch
    slug.py --path --mkdir       # ...and create .review-sessions/ if missing
    slug.py --path --dir /tmp/x  # ...under a different log directory
"""

import argparse
import os
import re
import subprocess
import sys

DEFAULT_LOG_DIR = ".review-sessions"

# Longest slug we emit. Leaves room for ".md" under the common 255-byte limit.
MAX_SLUG_LEN = 200

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_DASH_RUN = re.compile(r"-{2,}")
_DASH_AROUND_SEP = re.compile(r"-*__-*")


def slugify(branch):
    """Turn a branch name into a safe, flat filename stem."""
    name = branch.strip()
    if name.startswith("refs/heads/"):
        name = name[len("refs/heads/"):]

    slug = name.replace("/", "__")
    slug = _UNSAFE.sub("-", slug)
    slug = _DASH_RUN.sub("-", slug)
    slug = _DASH_AROUND_SEP.sub("__", slug)
    # No leading dot (would hide the log), no trailing dot or dash.
    slug = slug.strip("-").lstrip(".").rstrip(".")
    slug = slug[:MAX_SLUG_LEN].rstrip("-.")

    if not slug:
        raise ValueError("branch %r normalizes to an empty slug" % branch)
    return slug


def current_branch():
    """The checked-out branch name, or exit if HEAD is detached."""
    try:
        out = subprocess.check_output(
            ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except subprocess.CalledProcessError:
        sys.exit(
            "HEAD is detached (or this is not a git repo) — pass the branch "
            "name explicitly, e.g. `slug.py feature/payments`"
        )
    except (OSError, subprocess.SubprocessError) as err:
        sys.exit("could not read the current branch: %s" % err)
    return out.decode("utf-8", "replace").strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "branch",
        nargs="?",
        help="branch name to normalize (default: the current branch)",
    )
    parser.add_argument(
        "--path",
        action="store_true",
        help="print the full <dir>/<slug>.md log path instead of the bare slug",
    )
    parser.add_argument(
        "--dir",
        default=DEFAULT_LOG_DIR,
        help="log directory for --path (default: %s)" % DEFAULT_LOG_DIR,
    )
    parser.add_argument(
        "--mkdir",
        action="store_true",
        help="create the log directory if it is missing (implies --path)",
    )
    args = parser.parse_args(argv)

    branch = args.branch if args.branch else current_branch()
    try:
        slug = slugify(branch)
    except ValueError as err:
        sys.exit(str(err))

    if args.mkdir:
        args.path = True
        try:
            os.makedirs(args.dir, exist_ok=True)
        except OSError as err:
            sys.exit("could not create %s: %s" % (args.dir, err))

    print(os.path.join(args.dir, slug + ".md") if args.path else slug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
