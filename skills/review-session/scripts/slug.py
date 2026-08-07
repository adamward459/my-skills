#!/usr/bin/env python3
"""Normalize a git branch name into a `.review-sessions/` log slug.

The live thread log lives at `.review-sessions/<slug>.md`, one flat file per
branch. A branch name can contain `/` and other characters that are illegal or
awkward in a filename, so it gets normalized here rather than by hand:

    feature/payments        -> feature__payments.1a2b3c4d5e
    feat/JIRA-12 fix cache  -> feat__JIRA-12-fix-cache.3c4d5e6f7a
    release/v1.2.3          -> release__v1.2.3.9f8e7d6c5b

Every slug carries a short digest of the *full* branch name, so two branches
that happen to render the same readable prefix (`feature/payments` vs. the
literal `feature__payments`, or two long names that agree on their first 200
characters) never collide on one log file.

This script's only job is normalizing a branch name to a slug string. Joining
it into a path and creating the log directory is the caller's job. The branch
name is always required — the caller must pass the exact branch it's reviewing
rather than relying on whatever happens to be checked out.

Usage:
    slug.py feature/payments  # slug for the named branch
"""

import hashlib
import re
import sys

# Longest slug we emit. Leaves room for ".md" under the common 255-byte limit.
MAX_SLUG_LEN = 200

# Hex chars of the branch-name digest appended to every slug.
DIGEST_LEN = 10

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_DASH_RUN = re.compile(r"-{2,}")
_DASH_AROUND_SEP = re.compile(r"-*__-*")


def _readable_part(name):
    """Apply the character-safety transforms, without truncating or hashing."""
    slug = name.replace("/", "__")
    slug = _UNSAFE.sub("-", slug)
    slug = _DASH_RUN.sub("-", slug)
    slug = _DASH_AROUND_SEP.sub("__", slug)
    # No leading dot (would hide the log), no trailing dot or dash.
    slug = slug.strip("-").lstrip(".").rstrip(".")
    return slug


def _digest(name):
    """Stable digest of the full branch name, used to keep slugs collision-resistant."""
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:DIGEST_LEN]


def slugify(branch):
    """Turn a branch name into a safe, flat, collision-resistant filename stem."""
    name = branch.strip()
    if name.startswith("refs/heads/"):
        name = name[len("refs/heads/") :]
    if not name:
        raise ValueError("branch %r normalizes to an empty slug" % branch)

    suffix = "." + _digest(name)
    readable = _readable_part(name)[: MAX_SLUG_LEN - len(suffix)].rstrip("-.")
    return (readable + suffix) if readable else suffix.lstrip(".")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        sys.exit("usage: slug.py <branch>")

    try:
        print(slugify(argv[0]))
    except ValueError as err:
        sys.exit(str(err))
    return 0


if __name__ == "__main__":
    sys.exit(main())
