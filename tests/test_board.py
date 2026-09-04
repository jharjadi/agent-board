import json
import os
import subprocess
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

    def test_validate_id_rejects_trailing_newline(self):
        with self.assertRaises(ValueError):
            board.validate_id("123\n")
        with self.assertRaises(ValueError):
            board.validate_id("1\n2")


class TestCreate(unittest.TestCase):
    def test_creates_file_in_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = board.init_board(tmp)
            tid = board.create_ticket(root, "Fix login redirect loop", "body text")
            self.assertEqual(tid, "001")
            self.assertEqual(os.listdir(os.path.join(root, "todo")),
                             ["001-fix-login-redirect-loop.md"])

    def test_ids_increment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = board.init_board(tmp)
            self.assertEqual(board.create_ticket(root, "one"), "001")
            self.assertEqual(board.create_ticket(root, "two"), "002")
            self.assertEqual(board.create_ticket(root, "three"), "003")

    def test_ids_account_for_all_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = board.init_board(tmp)
            board.create_ticket(root, "a", column="done")
            self.assertEqual(board.create_ticket(root, "b"), "002")

    def test_concurrent_create_across_processes_never_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = board.init_board(tmp)
            code = (
                "import sys; sys.path.insert(0, %r); import board; "
                "print(board.create_ticket(%r, 'ticket ' + sys.argv[1]))"
            ) % (os.path.dirname(os.path.abspath(board.__file__)), root)
            procs = [subprocess.Popen([sys.executable, "-c", code, str(i)],
                                      stdout=subprocess.PIPE, text=True) for i in range(8)]
            ids = [p.communicate()[0].strip() for p in procs]
            self.assertEqual(len(ids), 8)
            self.assertEqual(len(set(ids)), 8, "duplicate ids: %s" % ids)

    def test_rejects_bad_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = board.init_board(tmp)
            with self.assertRaises(ValueError):
                board.create_ticket(root, "x", column="../escape")


class TestRead(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = board.init_board(self.tmp.name)
        board.create_ticket(self.root, "first task", "desc one")
        board.create_ticket(self.root, "second task", "desc two", column="review")

    def tearDown(self):
        self.tmp.cleanup()

    def test_find_ticket_returns_column_and_path(self):
        col, path = board.find_ticket(self.root, "2")
        self.assertEqual(col, "review")
        self.assertTrue(path.endswith("002-second-task.md"))

    def test_find_ticket_missing_raises(self):
        with self.assertRaises(KeyError):
            board.find_ticket(self.root, "99")

    def test_list_all(self):
        rows = board.list_tickets(self.root)
        self.assertEqual(len(rows), 2)
        self.assertEqual({c for c, _ in rows}, {"todo", "review"})

    def test_list_one_column(self):
        rows = board.list_tickets(self.root, "review")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1].title, "second task")

    def test_list_sorted_by_id(self):
        board.create_ticket(self.root, "third task")
        ids = [t.id for _, t in board.list_tickets(self.root)]
        self.assertEqual(ids, sorted(ids))

    def test_ticket_to_dict_shape(self):
        col, path = board.find_ticket(self.root, "1")
        d = board.ticket_to_dict(col, board.load_ticket(path))
        self.assertEqual(d["id"], "001")
        self.assertEqual(d["column"], "todo")
        self.assertEqual(d["title"], "first task")
        self.assertEqual(d["comments"], [])
        self.assertIn("created", d)


if __name__ == "__main__":
    unittest.main()
