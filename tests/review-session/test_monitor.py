#!/usr/bin/env python3
"""Tests for skills/review-session/scripts/monitor.py.

Kept out of the skill directory itself (skills/review-session/) since that
directory is what gets installed as the skill — tests aren't part of it.

main() is an infinite poll loop, so only the pure helpers are unit-tested;
argument handling is covered through the CLI. The per-poll event logic lives in
events_for() rather than inline in main() precisely so it can be tested here —
it runs outside the poll's try/except, where an unhandled shape would kill the
process rather than count against --max-errors.

Run with:
    python3 -m unittest discover -s tests -v     # from the repo root
    python3 tests/review-session/test_monitor.py # or directly
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "skills" / "review-session" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import monitor  # noqa: E402  (path setup must run first)

SCRIPT = SCRIPTS_DIR / "monitor.py"


class ExcerptTests(unittest.TestCase):
    def test_multiline_body_collapses_to_one_line(self):
        result = monitor.excerpt("first line\nsecond line\nthird")
        self.assertNotIn("\n", result)
        self.assertEqual(result, "first line second line third")

    def test_tabs_and_runs_of_whitespace_collapse(self):
        self.assertEqual(monitor.excerpt("a\t\tb   c\r\nd"), "a b c d")

    def test_leading_and_trailing_whitespace_stripped(self):
        self.assertEqual(monitor.excerpt("  padded  "), "padded")

    def test_truncates_to_body_excerpt(self):
        self.assertEqual(len(monitor.excerpt("x" * 500)), monitor.BODY_EXCERPT)

    def test_truncation_happens_after_collapsing(self):
        # Newlines must not consume excerpt budget as if they were content.
        body = "\n".join(["word"] * 200)
        self.assertNotIn("\n", monitor.excerpt(body))
        self.assertEqual(len(monitor.excerpt(body)), monitor.BODY_EXCERPT)

    def test_empty_and_whitespace_only_bodies(self):
        self.assertEqual(monitor.excerpt(""), "")
        self.assertEqual(monitor.excerpt("   \n\t "), "")

    def test_none_body_does_not_raise(self):
        self.assertEqual(monitor.excerpt(None), "")


class IsHumanTests(unittest.TestCase):
    def test_agent_author_excluded(self):
        self.assertFalse(monitor.is_human({"author": {"type": "agent"}}))

    def test_user_author_included(self):
        self.assertTrue(monitor.is_human({"author": {"type": "user"}}))

    def test_unknown_author_type_treated_as_human(self):
        # The exact non-agent string varies, so anything but "agent" counts.
        self.assertTrue(monitor.is_human({"author": {"type": "human"}}))

    def test_missing_author_treated_as_human(self):
        self.assertTrue(monitor.is_human({}))

    def test_missing_type_treated_as_human(self):
        self.assertTrue(monitor.is_human({"author": {}}))

    def test_null_author_treated_as_human(self):
        # A key present with an explicit null is not a missing key, so .get's
        # default never fires — this raised AttributeError before the `or {}`.
        self.assertTrue(monitor.is_human({"author": None}))


class LoadSeedFileTests(unittest.TestCase):
    def write_seed(self, payload):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        tmp.write(payload if isinstance(payload, str) else json.dumps(payload))
        tmp.close()
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        return tmp.name

    def test_round_trips_comment_ids_and_status(self):
        path = self.write_seed({"comment_ids": ["c1", "c2"], "status": {"t1": "open"}})
        seen, status = monitor.load_seed_file(path)
        self.assertEqual(seen, {"c1", "c2"})
        self.assertEqual(status, {"t1": "open"})

    def test_missing_keys_default_to_empty(self):
        seen, status = monitor.load_seed_file(self.write_seed({}))
        self.assertEqual(seen, set())
        self.assertEqual(status, {})

    def test_malformed_json_raises_value_error(self):
        with self.assertRaises(ValueError):
            monitor.load_seed_file(self.write_seed("{not json"))

    def test_missing_file_raises_os_error(self):
        with self.assertRaises(OSError):
            monitor.load_seed_file("/nonexistent/seed.json")


class SeedFromListingTests(unittest.TestCase):
    def test_seeds_human_comments_and_statuses(self):
        seen, status = monitor.seed_from_listing(
            [
                {
                    "id": "t1",
                    "status": "open",
                    "comments": [
                        {"id": "c1", "author": {"type": "user"}},
                        {"id": "c2", "author": {"type": "agent"}},
                    ],
                },
            ]
        )
        self.assertEqual(seen, {"c1"})  # the agent's own comment must not seed
        self.assertEqual(status, {"t1": "open"})

    def test_threads_and_comments_without_ids_are_skipped(self):
        seen, status = monitor.seed_from_listing(
            [
                {"status": "open", "comments": [{"author": {"type": "user"}}]},
            ]
        )
        self.assertEqual(seen, set())
        self.assertEqual(status, {})

    def test_null_comments_and_author_do_not_raise(self):
        # Explicit nulls, not absent keys: .get(k, default) returns None for these.
        seen, status = monitor.seed_from_listing(
            [
                {"id": "t1", "status": "open", "comments": None},
                {
                    "id": "t2",
                    "status": "open",
                    "comments": [
                        {"id": "c1", "author": None},
                    ],
                },
            ]
        )
        self.assertEqual(seen, {"c1"})
        self.assertEqual(status, {"t1": "open", "t2": "open"})


class EventsForTests(unittest.TestCase):
    def test_first_sighting_of_a_thread_is_not_a_status_change(self):
        status = {}
        lines = monitor.events_for([{"id": "t1", "status": "open"}], set(), status)
        self.assertEqual(lines, [])
        self.assertEqual(status, {"t1": "open"})

    def test_status_transition_fires_once(self):
        status = {"t1": "open"}
        thread = [{"id": "t1", "status": "resolved", "filePath": "a.py", "startLine": 3}]
        first = monitor.events_for(thread, set(), status)
        self.assertEqual(len(first), 1)
        self.assertIn("STATUS CHANGE [t1] a.py:3 — open → resolved", first[0])
        self.assertEqual(monitor.events_for(thread, set(), status), [])

    def test_new_human_comment_fires_once_then_is_seen(self):
        seen = set()
        thread = [
            {
                "id": "t1",
                "status": "open",
                "filePath": "a.py",
                "startLine": 7,
                "comments": [{"id": "c1", "author": {"type": "user"}, "body": "look here"}],
            }
        ]
        lines = monitor.events_for(thread, seen, {"t1": "open"})
        self.assertEqual(len(lines), 1)
        self.assertIn("NEW COMMENT [c1] a.py:7 — look here", lines[0])
        self.assertIn("c1", seen)
        self.assertEqual(monitor.events_for(thread, seen, {"t1": "open"}), [])

    def test_agent_comments_never_fire(self):
        lines = monitor.events_for(
            [
                {
                    "id": "t1",
                    "status": "open",
                    "comments": [{"id": "c1", "author": {"type": "agent"}, "body": "mine"}],
                }
            ],
            set(),
            {"t1": "open"},
        )
        self.assertEqual(lines, [])

    def test_thread_without_id_is_skipped_not_fatal(self):
        # Two id-less threads previously collided on a status[None] key and
        # emitted a bogus STATUS CHANGE.
        status = {}
        lines = monitor.events_for([{"status": "open"}, {"status": "resolved"}], set(), status)
        self.assertEqual(lines, [])
        self.assertEqual(status, {})

    def test_comment_without_id_does_not_raise(self):
        # Defensive reads are the first line of defence; the poll's try/except
        # is the second. Neither should be the only one.
        lines = monitor.events_for(
            [
                {
                    "id": "t1",
                    "status": "open",
                    "comments": [{"author": {"type": "user"}, "body": "no id"}],
                }
            ],
            set(),
            {"t1": "open"},
        )
        self.assertEqual(lines, [])

    def test_null_comments_list_does_not_raise(self):
        lines = monitor.events_for(
            [{"id": "t1", "status": "open", "comments": None}], set(), {"t1": "open"}
        )
        self.assertEqual(lines, [])

    def test_a_poll_that_raises_partway_absorbs_nothing(self):
        # events_for commits `seen`/`status` only after every thread parsed, so a
        # bad thread cannot leave earlier comments marked seen with their event
        # lines discarded — the caller retries and the next poll re-derives them.
        seen, status = set(), {}
        threads = [
            {
                "id": "t1",
                "status": "open",
                "filePath": "a.py",
                "startLine": 1,
                "comments": [{"id": "c1", "author": {"type": "user"}, "body": "first"}],
            },
            "not a dict",  # raises AttributeError partway through
        ]
        with self.assertRaises(AttributeError):
            monitor.events_for(threads, seen, status)
        self.assertEqual(seen, set())
        self.assertEqual(status, {})

        lines = monitor.events_for(threads[:1], seen, status)
        self.assertEqual(len(lines), 1)
        self.assertIn("NEW COMMENT [c1] a.py:1 — first", lines[0])

    def test_missing_file_and_line_fall_back_to_placeholders(self):
        lines = monitor.events_for(
            [
                {
                    "id": "t1",
                    "status": "open",
                    "comments": [{"id": "c1", "author": {"type": "user"}, "body": "x"}],
                }
            ],
            set(),
            {},
        )
        self.assertIn("unknown:?", lines[0])


class FetchThreadsTests(unittest.TestCase):
    def patch_output(self, payload):
        original = monitor.subprocess.check_output
        monitor.subprocess.check_output = lambda *a, **kw: json.dumps(payload).encode()
        self.addCleanup(lambda: setattr(monitor.subprocess, "check_output", original))

    def test_accepts_bare_list(self):
        self.patch_output([{"id": "t1"}])
        self.assertEqual(monitor.fetch_threads(), [{"id": "t1"}])

    def test_accepts_threads_wrapper(self):
        self.patch_output({"threads": [{"id": "t1"}]})
        self.assertEqual(monitor.fetch_threads(), [{"id": "t1"}])

    def test_object_without_threads_key_yields_empty(self):
        self.patch_output({"unexpected": 1})
        self.assertEqual(monitor.fetch_threads(), [])


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_rejects_poll_flag(self):
        # --poll was deliberately removed; the interval is fixed at POLL_SECONDS.
        proc = self.run_cli("--poll", "30")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--poll", proc.stderr)

    def test_rejects_missing_seed_file(self):
        # The seed file is read before the pre-flight `diffity agent list`, so
        # this must fail on the seed path specifically. Asserting only on the
        # exit code passed even when the run died at the pre-flight fetch
        # instead — green for the wrong reason on any machine without a live
        # diffity session.
        proc = self.run_cli("--seed-file", "/nonexistent/seed.json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--seed-file", proc.stderr)
        self.assertNotIn("diffity agent list", proc.stderr)

    def test_help_exits_zero_without_polling(self):
        proc = self.run_cli("--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--seed-file", proc.stdout)


if __name__ == "__main__":
    unittest.main()
