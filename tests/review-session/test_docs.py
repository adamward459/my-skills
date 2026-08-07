#!/usr/bin/env python3
"""Regression guard: the review-session skill declares no default base ref.

The base ref is a required argument of `/review-session`. A trunk name written
down anywhere in the skill silently reintroduces the failure it was removed to
prevent — a diffity session bound to the wrong ref in a repo whose trunk is
named something else, with every finding, thread, and log row built on the
wrong diff. Nothing errors, so only a test catches a re-added default.

Matches on the *marker phrases* a default is written with, never on the bare
word "main": the docs legitimately say "main session" and "main-loop"
throughout. `.review-sessions/` is git-ignored and out of scope.

Run with:
    python3 -m unittest discover -s tests -v   # from the repo root
    python3 tests/review-session/test_docs.py  # or directly
"""

import re
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "review-session"
SKILL_MD = SKILL_DIR / "SKILL.md"

# A default is written one of these ways. Each pattern is anchored on $BASE or
# <BASE> so an unrelated sentence about some other default can't trip it.
DEFAULT_DECLARATION_PATTERNS = [
    # "`$BASE` default: `main`." / "<BASE> default: main"
    re.compile(r"[`<]?\$?BASE[`>]?\s+default\s*:", re.IGNORECASE),
    # "default for `$BASE`" / "default base ref is ..."
    re.compile(r"default\s+(?:for\s+)?[`<]\$?BASE[`>]", re.IGNORECASE),
    # "$BASE defaults to main"
    re.compile(r"[`<]?\$?BASE[`>]?\s+defaults?\s+to\b", re.IGNORECASE),
]

# Auto-detecting the trunk is just a hidden default with the same failure mode,
# so the skill must not reach for these either.
TRUNK_DETECTION_PATTERNS = [
    re.compile(r"symbolic-ref"),
    re.compile(r"remotes?/origin/HEAD"),
    re.compile(r"remote\s+show\s+origin"),
    re.compile(r"merge-base"),
]

# A doc may *name* a rejected technique in order to forbid it. Tolerated only
# when the surrounding block also says not to do it, so the guard still catches
# someone adding one as an instruction.
PROHIBITION_MARKERS = ("never", "do not", "don't", "in disguise", "hidden default")


def skill_docs():
    return sorted(SKILL_DIR.rglob("*.md"))


def blocks(path):
    """Yield (start_lineno, text) per blank-line-separated block.

    Matching is per block, not per line: these docs wrap prose at ~100 columns,
    so a rule and the technique it forbids routinely land on different lines and
    a line-at-a-time check reads the second half as an instruction.
    """
    out, current, start = [], [], 1
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        if line.strip():
            if not current:
                start = lineno
            current.append(line.strip())
        elif current:
            out.append((start, " ".join(current)))
            current = []
    if current:
        out.append((start, " ".join(current)))
    return out


def offending_blocks(path, patterns, allow_prohibitions=False):
    hits = []
    for lineno, text in blocks(path):
        if not any(p.search(text) for p in patterns):
            continue
        if allow_prohibitions and any(m in text.lower() for m in PROHIBITION_MARKERS):
            continue
        hits.append(f"{path.name}:{lineno}: {text[:120]}")
    return hits


class NoDefaultBaseTests(unittest.TestCase):
    def test_docs_exist(self):
        # A rglob that silently matches nothing would make every other test
        # here pass vacuously.
        self.assertTrue(SKILL_MD.is_file())
        self.assertGreater(len(skill_docs()), 1)

    def test_no_doc_declares_a_default_base(self):
        hits = []
        for path in skill_docs():
            hits += offending_blocks(path, DEFAULT_DECLARATION_PATTERNS)
        self.assertEqual(hits, [], "a default base ref was re-added:\n" + "\n".join(hits))

    def test_no_doc_auto_detects_the_trunk(self):
        hits = []
        for path in skill_docs():
            hits += offending_blocks(path, TRUNK_DETECTION_PATTERNS, allow_prohibitions=True)
        self.assertEqual(hits, [], "trunk auto-detection was added:\n" + "\n".join(hits))

    def test_repo_bindings_says_base_has_no_default(self):
        # The table is where a default lived and where one would come back, so
        # the absence has to be stated there, not merely implied.
        text = SKILL_MD.read_text()
        bindings = text.split("## Repo bindings", 1)
        self.assertEqual(len(bindings), 2, "SKILL.md lost its Repo bindings section")
        self.assertRegex(bindings[1], r"\$BASE.{0,40}no\*{0,2}\s*default")


class RequiredArgumentTests(unittest.TestCase):
    def test_argument_hint_marks_base_as_required(self):
        # Angle brackets mean required; square brackets mean optional and would
        # advertise the removed default back to the user.
        hint = None
        for line in SKILL_MD.read_text().splitlines():
            if line.startswith("argument-hint:"):
                hint = line.split(":", 1)[1].strip().strip('"').strip("'")
                break
        self.assertIsNotNone(hint, "SKILL.md has no argument-hint in its frontmatter")
        self.assertTrue(
            hint.startswith("<") and hint.endswith(">"), f"argument-hint is optional: {hint}"
        )

    def test_invocation_calls_the_base_required(self):
        self.assertIn("one required argument: the base ref", SKILL_MD.read_text())

    def test_no_never_prompt_instruction_remains(self):
        # The old text told the agent to fall back silently rather than ask.
        # With no default left, the fallback has nowhere to go.
        self.assertNotIn("Never prompt for it", SKILL_MD.read_text())


if __name__ == "__main__":
    unittest.main()
