#!/usr/bin/env python3
"""Tests for skills/review-session/scripts/slug.py.

Kept out of the skill directory itself (skills/review-session/) since that
directory is what gets installed as the skill — tests aren't part of it.

Run with:
    python3 -m unittest discover -s tests -v   # from the repo root
    python3 tests/review-session/test_slug.py  # or directly
"""

import subprocess
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "skills" / "review-session" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import slug  # noqa: E402  (path setup must run first)

SCRIPT = SCRIPTS_DIR / "slug.py"


class SlugifyTests(unittest.TestCase):
    def test_readable_prefix_is_preserved(self):
        self.assertTrue(slug.slugify("feature/payments").startswith("feature__payments."))

    def test_digest_suffix_is_ten_hex_chars(self):
        result = slug.slugify("feature/payments")
        digest = result.rsplit(".", 1)[1]
        self.assertEqual(len(digest), slug.DIGEST_LEN)
        int(digest, 16)  # raises ValueError if not hex

    def test_deterministic(self):
        self.assertEqual(slug.slugify("feature/payments"), slug.slugify("feature/payments"))

    def test_slash_and_literal_double_underscore_do_not_collide(self):
        # Previously both normalized to the same "feature__payments" slug.
        self.assertNotEqual(slug.slugify("feature/payments"), slug.slugify("feature__payments"))

    def test_long_names_sharing_a_200_char_prefix_do_not_collide(self):
        # Previously both were truncated to the same 200-char slug.
        a = "x" * 200 + "-branchA"
        b = "x" * 200 + "-branchB"
        self.assertNotEqual(slug.slugify(a), slug.slugify(b))

    def test_refs_heads_prefix_is_stripped(self):
        self.assertEqual(slug.slugify("refs/heads/feature/payments"), slug.slugify("feature/payments"))

    def test_unsafe_characters_become_dashes(self):
        result = slug.slugify("feat/JIRA-12 fix cache")
        self.assertEqual(result.split(".")[0], "feat__JIRA-12-fix-cache")

    def test_repeated_dashes_collapse(self):
        result = slug.slugify("feat//weird***name")
        self.assertNotIn("---", result)
        self.assertNotIn("...", result.replace("." + result.rsplit(".", 1)[1], ""))

    def test_result_never_exceeds_max_slug_len_plus_digest(self):
        result = slug.slugify("y" * 500)
        self.assertLessEqual(len(result), slug.MAX_SLUG_LEN)

    def test_blank_branch_raises(self):
        with self.assertRaises(ValueError):
            slug.slugify("   ")

    def test_branch_that_normalizes_to_empty_readable_part_still_slugs(self):
        # "///" strips down to nothing but whitespace/slashes; the digest alone
        # must still produce a non-empty, valid slug rather than raising.
        result = slug.slugify("///")
        self.assertTrue(result)
        self.assertNotEqual(result, ".")


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_prints_slug_for_named_branch(self):
        proc = self.run_cli("feature/payments")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), slug.slugify("feature/payments"))

    def test_rejects_extra_arguments(self):
        proc = self.run_cli("feature/payments", "extra")
        self.assertNotEqual(proc.returncode, 0)

    def test_rejects_missing_branch(self):
        # No current-branch fallback: the caller must always pass one.
        proc = self.run_cli()
        self.assertNotEqual(proc.returncode, 0)

    def test_rejects_blank_branch(self):
        proc = self.run_cli("   ")
        self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
