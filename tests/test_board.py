import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import board

SAMPLE = '''---
id: "007"
title: Fix login redirect loop
owner: codex
created: 2026-09-04T13:53:00Z
branch: fix/login-redirect
---

Session cookie isn't cleared on 401.
Repro: log in, revoke session, refresh.

## comment — claude · 2026-09-04T14:10:00Z
Fixed in a1b2c3d.

## comment — codex · 2026-09-04T14:40:00Z
Reviewed. No regression test.
'''


class TestParsing(unittest.TestCase):
    def test_parses_all_fields(self):
        t = board.parse_ticket(SAMPLE)
        self.assertEqual(t.id, "007")
        self.assertEqual(t.title, "Fix login redirect loop")
        self.assertEqual(t.owner, "codex")
        self.assertEqual(t.branch, "fix/login-redirect")
        self.assertIn("Repro: log in", t.description)
        self.assertEqual(len(t.comments), 2)
        self.assertEqual(t.comments[0].by, "claude")
        self.assertEqual(t.comments[1].body, "Reviewed. No regression test.")

    def test_round_trip_is_stable(self):
        t = board.parse_ticket(SAMPLE)
        again = board.parse_ticket(board.render_ticket(t))
        self.assertEqual(t, again)

    def test_optional_fields_absent(self):
        text = '---\nid: "001"\ntitle: Bare\ncreated: 2026-09-04T00:00:00Z\n---\n\nBody.\n'
        t = board.parse_ticket(text)
        self.assertIsNone(t.owner)
        self.assertIsNone(t.branch)
        self.assertEqual(t.comments, [])

    def test_rejects_missing_frontmatter(self):
        with self.assertRaises(ValueError):
            board.parse_ticket("no frontmatter here")

    def test_rejects_missing_required_field(self):
        with self.assertRaises(ValueError):
            board.parse_ticket('---\nid: "001"\n---\n\nbody\n')


class TestLayout(unittest.TestCase):
    def test_init_creates_all_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = board.init_board(tmp)
            for col in board.COLUMNS:
                self.assertTrue(os.path.isdir(os.path.join(root, col)), col)

    def test_init_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            board.init_board(tmp)
            board.init_board(tmp)
            self.assertTrue(os.path.isdir(os.path.join(tmp, board.BOARD_DIR, "todo")))

    def test_find_root_walks_upward(self):
        with tempfile.TemporaryDirectory() as tmp:
            board.init_board(tmp)
            nested = os.path.join(tmp, "a", "b")
            os.makedirs(nested)
            self.assertEqual(
                os.path.realpath(board.find_root(nested)),
                os.path.realpath(os.path.join(tmp, board.BOARD_DIR)),
            )

    def test_find_root_exits_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                board.find_root(tmp)

    def test_validate_id_rejects_traversal(self):
        for bad in ["../etc", "1/../2", "abc", "", "1234567", "007/x"]:
            with self.assertRaises(ValueError, msg=bad):
                board.validate_id(bad)
        self.assertEqual(board.validate_id("7"), "007")
        self.assertEqual(board.validate_id("007"), "007")

    def test_validate_column_rejects_unknown(self):
        with self.assertRaises(ValueError):
            board.validate_column("../../tmp")
        with self.assertRaises(ValueError):
            board.validate_column("backlog")
        self.assertEqual(board.validate_column("todo"), "todo")

    def test_slugify(self):
        self.assertEqual(board.slugify("Fix login redirect loop!"), "fix-login-redirect-loop")
        self.assertEqual(board.slugify("  A/B  test  "), "a-b-test")

    def test_atomic_write_replaces_and_leaves_no_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "f.md")
            board.atomic_write(p, "one")
            board.atomic_write(p, "two")
            with open(p) as fh:
                self.assertEqual(fh.read(), "two")
            self.assertEqual(os.listdir(tmp), ["f.md"])


if __name__ == "__main__":
    unittest.main()
