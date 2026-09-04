import contextlib
import http.client
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
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

    def test_concurrent_create_across_columns_never_duplicates(self):
        """Reservation must be per-id, not per-(column, id): racing into
        DIFFERENT columns must still never hand out the same id twice."""
        with tempfile.TemporaryDirectory() as tmp:
            root = board.init_board(tmp)
            code = (
                "import sys; sys.path.insert(0, %r); import board; "
                "print(board.create_ticket(%r, 'ticket ' + sys.argv[1], column=sys.argv[2]))"
            ) % (os.path.dirname(os.path.abspath(board.__file__)), root)
            procs = [
                subprocess.Popen(
                    [sys.executable, "-c", code, str(i), board.COLUMNS[i % len(board.COLUMNS)]],
                    stdout=subprocess.PIPE, text=True,
                )
                for i in range(8)
            ]
            ids = [p.communicate()[0].strip() for p in procs]
            self.assertEqual(len(ids), 8)
            self.assertEqual(len(set(ids)), 8, "duplicate ids: %s" % ids)

    def test_rejects_bad_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = board.init_board(tmp)
            with self.assertRaises(ValueError):
                board.create_ticket(root, "x", column="../escape")

    def test_title_with_frontmatter_injection_is_sanitized(self):
        """A newline-laden title must not be able to terminate frontmatter
        early and corrupt the file for every later reader."""
        with tempfile.TemporaryDirectory() as tmp:
            root = board.init_board(tmp)
            evil_title = "evil\n---\nowner: x"
            tid = board.create_ticket(root, evil_title)
            _, path = board.find_ticket(root, tid)
            # Must still parse.
            t = board.load_ticket(path)
            self.assertNotIn("\n", t.title)
            self.assertEqual(t.owner, None)
            # Must still list.
            rows = board.list_tickets(root)
            self.assertEqual(len(rows), 1)
            # Must not break the web UI, which loads every file.
            page = board.render_board_html(root)
            self.assertIn("evil", page)

    def test_owner_with_newline_is_sanitized_on_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = board.init_board(tmp)
            tid = board.create_ticket(root, "task", owner="mal\nlory")
            _, path = board.find_ticket(root, tid)
            t = board.load_ticket(path)
            self.assertNotIn("\n", t.owner)


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

    def test_list_sorted_numerically_past_999(self):
        for tid, title in (("002", "two"), ("1000", "thousand")):
            t = board.Ticket(id=tid, title=title, created=board.utc_now(), description="d")
            path = os.path.join(self.root, "todo", f"{tid}-{board.slugify(title)}.md")
            board.atomic_write(path, board.render_ticket(t))
        ids = [t.id for _, t in board.list_tickets(self.root, "todo")]
        self.assertLess(ids.index("002"), ids.index("1000"))

    def test_find_ticket_with_bare_file(self):
        # Create a bare ticket file (crashed during creation)
        t = board.Ticket(id="007", title="bare ticket", created=board.utc_now(), description="d")
        path = os.path.join(self.root, "doing", "007.md")
        board.atomic_write(path, board.render_ticket(t))
        # find_ticket should locate it via fallback pattern
        col, found_path = board.find_ticket(self.root, "7")
        self.assertEqual(col, "doing")
        self.assertTrue(found_path.endswith("007.md"))


class TestMutate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = board.init_board(self.tmp.name)
        board.create_ticket(self.root, "a task", "desc")

    def tearDown(self):
        self.tmp.cleanup()

    def test_move_changes_column_and_leaves_one_file(self):
        board.move_ticket(self.root, "1", "review")
        self.assertEqual(os.listdir(os.path.join(self.root, "todo")), [])
        self.assertEqual(len(os.listdir(os.path.join(self.root, "review"))), 1)
        col, _ = board.find_ticket(self.root, "1")
        self.assertEqual(col, "review")

    def test_move_rejects_bad_column(self):
        with self.assertRaises(ValueError):
            board.move_ticket(self.root, "1", "nowhere")

    def test_take_sets_owner_and_moves_to_doing(self):
        board.take_ticket(self.root, "1", "codex")
        col, path = board.find_ticket(self.root, "1")
        self.assertEqual(col, "doing")
        self.assertEqual(board.load_ticket(path).owner, "codex")

    def test_take_sanitizes_owner_with_newline(self):
        board.take_ticket(self.root, "1", "evil\n---\nbranch: hijacked")
        _, path = board.find_ticket(self.root, "1")
        t = board.load_ticket(path)
        self.assertNotIn("\n", t.owner)
        self.assertIsNone(t.branch)

    def test_comment_appends_and_preserves_description(self):
        board.add_comment(self.root, "1", "first note", "claude")
        board.add_comment(self.root, "1", "second note", "codex")
        _, path = board.find_ticket(self.root, "1")
        t = board.load_ticket(path)
        self.assertEqual(len(t.comments), 2)
        self.assertEqual(t.comments[0].by, "claude")
        self.assertEqual(t.comments[1].body, "second note")
        self.assertEqual(t.description, "desc")

    def test_comment_survives_move(self):
        board.add_comment(self.root, "1", "note", "claude")
        board.move_ticket(self.root, "1", "done")
        _, path = board.find_ticket(self.root, "1")
        self.assertEqual(len(board.load_ticket(path).comments), 1)

    def test_concurrent_comments_are_not_lost(self):
        board.create_ticket(self.root, "concurrent target", "desc")
        mod_dir = os.path.dirname(os.path.abspath(board.__file__))
        code = (
            "import sys; sys.path.insert(0, %r); import board; "
            "board.add_comment(%r, '1', 'note ' + sys.argv[1], 'agent' + sys.argv[1])"
        ) % (mod_dir, self.root)
        procs = [subprocess.Popen([sys.executable, "-c", code, str(i)]) for i in range(8)]
        for p in procs:
            p.wait()
        _, path = board.find_ticket(self.root, "1")
        self.assertEqual(len(board.load_ticket(path).comments), 8)


class TestWatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = board.init_board(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_ticket_is_a_change(self):
        changed, seen = board.watch_once(self.root, "todo", {})
        self.assertEqual(changed, [])
        board.create_ticket(self.root, "hello")
        changed, seen = board.watch_once(self.root, "todo", seen)
        self.assertEqual(changed, ["001"])

    def test_unchanged_emits_nothing(self):
        board.create_ticket(self.root, "hello")
        _, seen = board.watch_once(self.root, "todo", {})
        changed, _ = board.watch_once(self.root, "todo", seen)
        self.assertEqual(changed, [])

    def test_touched_ticket_marks_changed(self):
        board.create_ticket(self.root, "hello")
        _, seen = board.watch_once(self.root, "todo", {})
        os.utime(board.find_ticket(self.root, "1")[1], (2000000000, 2000000000))
        changed, _ = board.watch_once(self.root, "todo", seen)
        self.assertEqual(changed, ["001"])

    def test_move_out_is_not_a_change_in_source(self):
        board.create_ticket(self.root, "hello")
        _, seen = board.watch_once(self.root, "todo", {})
        board.move_ticket(self.root, "1", "review")
        changed, _ = board.watch_once(self.root, "todo", seen)
        self.assertEqual(changed, [])

    def test_first_call_with_empty_seen_reports_existing(self):
        board.create_ticket(self.root, "already here")
        changed, seen = board.watch_once(self.root, "todo", {})
        self.assertEqual(changed, ["001"])
        self.assertIn("001", seen)

    def test_watch_sorted_numerically_past_999(self):
        col = os.path.join(self.root, "todo")
        for tid in ["001", "009", "099", "999", "1000", "1001"]:
            path = os.path.join(col, "%s-test.md" % tid)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("---\nid: %r\ntitle: test\ncreated: 2000-01-01T00:00:00Z\n---\n" % tid)
        changed, _ = board.watch_once(self.root, "todo", {})
        self.assertEqual(changed, ["001", "009", "099", "999", "1000", "1001"])


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = board.main(list(args))
        return code, out.getvalue()

    def test_init_then_new_then_list_json(self):
        self.run_cli("init")
        code, _ = self.run_cli("new", "first task", "--desc", "some detail")
        self.assertEqual(code, 0)
        code, out = self.run_cli("list", "--json")
        data = json.loads(out)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["title"], "first task")
        self.assertEqual(data[0]["column"], "todo")

    def test_take_and_comment_and_show(self):
        self.run_cli("init")
        self.run_cli("new", "a task")
        self.run_cli("take", "1", "--owner", "codex")
        self.run_cli("comment", "1", "looks fine", "--by", "claude")
        _, out = self.run_cli("show", "1", "--json")
        data = json.loads(out)
        self.assertEqual(data["column"], "doing")
        self.assertEqual(data["owner"], "codex")
        self.assertEqual(data["comments"][0]["body"], "looks fine")

    def test_move_command(self):
        self.run_cli("init")
        self.run_cli("new", "a task")
        self.run_cli("move", "1", "done")
        _, out = self.run_cli("show", "1", "--json")
        self.assertEqual(json.loads(out)["column"], "done")

    def test_unknown_id_exits_nonzero(self):
        self.run_cli("init")
        code, _ = self.run_cli("show", "42")
        self.assertNotEqual(code, 0)

    def test_bad_column_exits_nonzero(self):
        self.run_cli("init")
        self.run_cli("new", "a task")
        code, _ = self.run_cli("move", "1", "backlog")
        self.assertNotEqual(code, 0)

    def test_unwritable_board_exits_two_not_traceback(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores permission bits")
        self.run_cli("init")
        todo = os.path.join(os.getcwd(), board.BOARD_DIR, "todo")
        os.chmod(todo, 0o500)
        try:
            code, _ = self.run_cli("new", "should fail cleanly")
            self.assertEqual(code, 2)
        finally:
            os.chmod(todo, 0o700)


class TestWebUI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = board.init_board(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_renders_all_columns(self):
        page = board.render_board_html(self.root)
        for col in board.COLUMNS:
            self.assertIn(col, page)

    def test_renders_ticket_title(self):
        board.create_ticket(self.root, "visible title")
        self.assertIn("visible title", board.render_board_html(self.root))

    def test_escapes_html_in_title(self):
        board.create_ticket(self.root, "<script>alert(1)</script>")
        page = board.render_board_html(self.root)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_state_reconstructs_from_disk(self):
        board.create_ticket(self.root, "one")
        before = board.render_board_html(self.root)
        board.move_ticket(self.root, "1", "done")
        after = board.render_board_html(self.root)
        self.assertNotEqual(before, after)
        self.assertEqual(after, board.render_board_html(self.root))

    def test_malformed_content_length_returns_400(self):
        """Malformed Content-Length header should return HTTP 400, not hang."""
        handler = board._make_handler(self.root)
        server = board.HTTPServer(("127.0.0.1", 0), handler)
        port = server.server_port
        srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
        srv_thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request("POST", "/new", "", {"Content-Length": "abc"})
            resp = conn.getresponse()
            self.assertEqual(resp.status, 400)
            conn.close()
        finally:
            server.shutdown()

    def test_oversized_content_length_returns_400(self):
        """Content-Length larger than MAX_BODY_BYTES should return HTTP 400."""
        handler = board._make_handler(self.root)
        server = board.HTTPServer(("127.0.0.1", 0), handler)
        port = server.server_port
        srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
        srv_thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request("POST", "/new", "", {"Content-Length": str(board.MAX_BODY_BYTES + 1)})
            resp = conn.getresponse()
            self.assertEqual(resp.status, 400)
            conn.close()
        finally:
            server.shutdown()

    def test_valid_post_still_works(self):
        """Valid POST to /move should return 303 and move the ticket."""
        board.create_ticket(self.root, "test task")
        handler = board._make_handler(self.root)
        server = board.HTTPServer(("127.0.0.1", 0), handler)
        port = server.server_port
        srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
        srv_thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port)
            body = "id=001&column=done"
            conn.request("POST", "/move", body, {"Content-Length": str(len(body))})
            resp = conn.getresponse()
            self.assertEqual(resp.status, 303)
            self.assertEqual(resp.getheader("Location"), "/")
            conn.close()
            # Verify ticket was actually moved
            col, _ = board.find_ticket(self.root, "1")
            self.assertEqual(col, "done")
        finally:
            server.shutdown()

    def test_corrupt_ticket_file_returns_500_not_dead_connection(self):
        """A deliberately corrupt .md file should yield an HTTP error
        response from do_GET, not a dead connection plus a traceback."""
        board.create_ticket(self.root, "fine ticket")
        corrupt_path = os.path.join(self.root, "todo", "999-corrupt.md")
        with open(corrupt_path, "w", encoding="utf-8") as fh:
            fh.write("this is not valid frontmatter at all")
        handler = board._make_handler(self.root)
        server = board.HTTPServer(("127.0.0.1", 0), handler)
        port = server.server_port
        srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
        srv_thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request("GET", "/")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 500)
            resp.read()
            conn.close()
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
