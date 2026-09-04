import os, sys, unittest
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


if __name__ == "__main__":
    unittest.main()
