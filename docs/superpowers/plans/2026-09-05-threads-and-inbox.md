# Threads and Inbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let agents hold persistent, addressed conversations on the board, with or without a ticket, and let any agent ask "what is waiting on me" and get an answer computed from the files.

**Architecture:** A thread is a ticket-shaped markdown file under `.agent-board/threads/` with no column. Tickets and threads share one message shape: the existing comment header gains optional trailers `to`, `ask`, `re <list>`, and `commit`. Pending is derived on read: an ask is pending until a later message in the same file lists it in `re`. No state files, no counters, no message status. The web UI renders threads, badges, and a waiting strip from the same files.

**Tech Stack:** Python 3.11+ standard library only. `unittest` for tests, `http.server` for the UI. Everything in one file, `board.py`.

**Spec:** `docs/superpowers/specs/2026-09-05-threads-and-inbox-design.md`. Read it before starting; the plan argues from it. The original board spec at `docs/superpowers/specs/2026-09-04-agent-board-design.md` still holds.

## Global Constraints

- **Python 3.11+.** Use `X | None`, `list[int]`, `dict[str, str]`. Do not import from `typing`.
- **Run tests with `python3.14 -m unittest discover -s tests -v`.** Bare `python3` on this machine is 3.9.6 and will fail on the syntax. Any interpreter >= 3.11 is fine.
- **Standard library only.** No third-party imports in `board.py` or `tests/`.
- **Single file.** All implementation lives in `board.py` at the repo root. All tests live in `tests/test_board.py`.
- **Never nest `board_lock`.** `fcntl.flock` on a second descriptor to the same file blocks even inside one process. Functions named `_something_locked` assume the caller holds the lock and never take it. Public functions take it once.
- **Columns are fixed:** `todo`, `doing`, `review`, `blocked`, `done`. `threads` is a directory, never a column; `validate_column` must keep rejecting it.
- **The web UI is a projection.** Every POST calls the same function the CLI calls. A hard refresh rebuilds the page from disk.
- **Editing `board.py` needs a `board serve` restart.** The CLI picks up changes at once.
- **Commit after every task** with a message in the repo's existing style: a short imperative subject, a body that says why. The existing log is the style guide (`git log --oneline -20`).
- Every step that says "Run" expects you to actually run it and read the output. "Expected" lines say what you should see.

## File Structure

The codebase is deliberately one file, so decomposition is by section inside `board.py`, in this order, matching where the existing code already lives:

| Section of `board.py` | Responsibility | Tasks |
|---|---|---|
| Data model and parsing (top, `Comment`, `Ticket`, `COMMENT_RE`, `parse_ticket`, `render_ticket`) | Header grammar with trailers, `ticket` frontmatter field, render | 2, 3 |
| Sanitising and locking (`sanitize_scalar`, `board_lock`, `init_board`) | `sanitize_name`, `neutralise_body`, `threads/` in init | 2, 3 |
| Creation and lookup (`next_id`, `create_ticket`, `find_ticket`, `list_tickets`) | Allocator under lock, ambiguity refusal, threads in scan and lookup, `create_thread`, `list_threads` | 1, 3 |
| Mutation (`_move_locked`, `take_ticket`, `set_owner`, `add_comment`) | Thread refusal, `add_comment` with trailers and single-write append | 2, 3 |
| Queries (new, after `watch_once`) | `pending_asks`, `_all_items`, `inbox_rows`, `_first_line` | 4 |
| Web UI (`PAGE_CSS`, `_render_card`, `render_board_html`, `_make_handler`) | Badges, threads section, waiting strip, forms, `/thread` POST, reload fix | 5 |
| CLI (`main`) | `thread`, `threads`, `inbox`, `comment` flags, `show --last`, `--body-file` | 4 |
| Agents block (`agents_block`) | Updated instructions | 6 |

Tests go into `tests/test_board.py` as new `unittest.TestCase` classes appended at the end, one class per task, named in each task.

---

### Task 1: Allocator under the lock, and unambiguous lookup

The id scan in `create_ticket` runs outside the board lock. Two creators can both compute 12; the first reserves `012.md` at the root and renames it into a column; the second then reserves the now-absent `012.md` and also wins 12. This task is first because everything after it allocates through this code.

**Files:**
- Modify: `board.py` — `create_ticket` (around line 300), `find_ticket` (around line 325)
- Test: `tests/test_board.py` — new class `TestAllocatorLock` appended at the end

**Interfaces:**
- Consumes: `board_lock(root)`, `next_id(root)`, `render_ticket`, `slugify`, `sanitize_scalar`, `validate_column`, `validate_id`.
- Produces: `_create_locked(root: str, title: str, description: str, dest_dir: str, owner: str | None = None) -> tuple[str, str]` returning `(id, final_path)`; caller holds the lock. `create_ticket` keeps its signature and return type (`str`). `find_ticket(root, tid) -> tuple[str, str]` now raises `ValueError` when an id matches more than one file.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_board.py`. Add `import time` and `from unittest import mock` to the imports at the top of the file (keep them alphabetical with the others).

```python
class TestAllocatorLock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = board.init_board(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_and_reserve_happen_under_the_lock(self):
        """Two creators that both scan before either reserves must still get
        different ids. Without the lock the first sleeps after scanning, the
        second scans the same number, reserves and renames away, and the first
        then reserves the now-absent root file: both win the same id."""
        real_next_id = board.next_id
        first = threading.Event()

        def slow_next_id(root):
            tid = real_next_id(root)
            if not first.is_set():
                first.set()
                time.sleep(0.3)
            return tid

        ids = []
        with mock.patch.object(board, "next_id", slow_next_id):
            workers = [
                threading.Thread(target=lambda i=i: ids.append(board.create_ticket(self.root, "t%d" % i)))
                for i in range(2)
            ]
            for w in workers:
                w.start()
            for w in workers:
                w.join()
        self.assertEqual(sorted(ids), ["001", "002"], ids)

    def test_find_ticket_refuses_an_ambiguous_id(self):
        for col in ("todo", "done"):
            t = board.Ticket(id="005", title="dup", created=board.utc_now())
            board.atomic_write(os.path.join(self.root, col, "005-dup.md"), board.render_ticket(t))
        with self.assertRaises(ValueError):
            board.find_ticket(self.root, "5")

    def test_find_ticket_still_finds_a_unique_bare_file(self):
        t = board.Ticket(id="007", title="bare", created=board.utc_now())
        board.atomic_write(os.path.join(self.root, "doing", "007.md"), board.render_ticket(t))
        col, path = board.find_ticket(self.root, "7")
        self.assertEqual(col, "doing")
        self.assertTrue(path.endswith("007.md"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3.14 -m unittest tests.test_board.TestAllocatorLock -v`
Expected: `test_scan_and_reserve_happen_under_the_lock` FAILS with `['001', '001']` (or similar duplicate). `test_find_ticket_refuses_an_ambiguous_id` FAILS because no `ValueError` is raised. The bare-file test passes already.

- [ ] **Step 3: Move the allocation loop into a locked helper**

In `board.py`, replace the whole `create_ticket` function with these two functions:

```python
def _create_locked(root: str, title: str, description: str, dest_dir: str,
                   owner: str | None = None) -> tuple[str, str]:
    """Allocate an id and write a new file into dest_dir. Caller holds the board lock.

    The scan in next_id and the O_EXCL reservation must sit in one critical
    section. Reservation alone only guards the window between reserve and
    rename; a creator that scanned before another's rename can still reserve
    the same number afterwards. That happened in review, with a simulation.
    """
    title = sanitize_scalar(title)
    owner = sanitize_scalar(owner) if owner else None
    for _ in range(MAX_ID_ATTEMPTS):
        tid = next_id(root)
        # Reserve at the board ROOT, not inside the destination: exclusivity
        # must be on the id alone, or two creates into different directories
        # can both win the same id.
        reservation_path = os.path.join(root, "%s.md" % tid)
        try:
            fd = os.open(reservation_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            continue
        try:
            ticket = Ticket(id=tid, title=title, created=utc_now(),
                            description=description, owner=owner)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(render_ticket(ticket))
            final_path = os.path.join(root, dest_dir, "%s-%s.md" % (tid, slugify(title)))
            os.makedirs(os.path.dirname(final_path), exist_ok=True)
            os.rename(reservation_path, final_path)
            return tid, final_path
        except BaseException:
            if os.path.exists(reservation_path):
                os.unlink(reservation_path)
            raise
    raise RuntimeError("could not allocate an id after %d attempts" % MAX_ID_ATTEMPTS)


def create_ticket(root: str, title: str, description: str = "",
                  column: str = "todo", owner: str | None = None) -> str:
    column = validate_column(column)
    with board_lock(root):
        tid, _ = _create_locked(root, title, description, column, owner)
    return tid
```

- [ ] **Step 4: Make `find_ticket` collect every match and refuse more than one**

Replace `find_ticket`:

```python
def find_ticket(root: str, tid: str) -> tuple[str, str]:
    """Locate a ticket by id. Exactly one file may carry an id; two is an error,
    not a coin toss, because every mutation would otherwise act on whichever
    sorted first and leave the other as a silent twin."""
    tid = validate_id(tid)
    found: list[tuple[str, str]] = []
    for col in COLUMNS:
        for pattern in ("%s-*.md" % tid, "%s.md" % tid):
            for path in sorted(glob.glob(os.path.join(root, col, pattern))):
                found.append((col, path))
    if not found:
        raise KeyError("no ticket with id %s" % tid)
    if len(found) > 1:
        raise ValueError("id %s matches %d files: %s"
                         % (tid, len(found), ", ".join(p for _, p in found)))
    return found[0]
```

- [ ] **Step 5: Run the new tests and the whole suite**

Run: `python3.14 -m unittest tests.test_board.TestAllocatorLock -v`
Expected: 3 tests PASS.

Run: `python3.14 -m unittest discover -s tests -v 2>&1 | tail -5`
Expected: `OK` with 97 tests (94 existing plus 3).

- [ ] **Step 6: Commit**

```bash
git add board.py tests/test_board.py
git commit -m "fix: allocate ids under the board lock; refuse ambiguous ids

next_id scanned outside the lock, so two creators could both compute the
same number, and the second could reserve the root file after the first
had renamed it away. find_ticket also returned the first of several
matches instead of saying so."
```

---

### Task 2: One message shape with trailers

The comment header gains optional trailers. Parsing anchors on the timestamp, so old files and old authors containing `·` parse the same as before. `add_comment` learns the new fields, sanitises names, validates `re` under the lock, neutralises header-shaped body lines, and appends with one write loop.

**Files:**
- Modify: `board.py` — `COMMENT_RE` and `Comment` (top of file), `parse_ticket`, `render_ticket`, `sanitize_scalar` area, `add_comment`
- Modify: `docs/superpowers/specs/2026-09-05-threads-and-inbox-design.md` — one sentence, see Step 8
- Test: `tests/test_board.py` — new class `TestMessageTrailers`

**Interfaces:**
- Consumes: `board_lock`, `find_ticket`, `load_ticket`, `utc_now`, `sanitize_scalar`.
- Produces:
  - `Comment(by, at, body, to: str | None = None, ask: bool = False, re: list[int] = [], commit: str | None = None)` — new fields default so every existing `Comment(by, at, body)` call still works.
  - `render_comment_header(c: Comment) -> str`
  - `sanitize_name(value: str) -> str` — `sanitize_scalar` plus removal of `·`.
  - `neutralise_body(body: str) -> str`
  - `_prepare_comment(body, by, to, ask, refs, commit) -> Comment` — validation that needs no lock.
  - `_append_comment_locked(path: str, comment: Comment) -> int` — validates `re` against the file, appends, returns the new message number. Caller holds the lock.
  - `add_comment(root, tid, body, by, to=None, ask=False, refs=None, commit=None) -> int` — returns the new message number.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_board.py`:

```python
OLD_AUTHOR_WITH_SEPARATOR = '''---
id: "001"
title: Old file
created: 2026-09-04T00:00:00Z
---

body

## comment — odd · name · 2026-09-04T14:10:00Z
hello
'''


class TestMessageTrailers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = board.init_board(self.tmp.name)
        board.create_ticket(self.root, "target", "desc")

    def tearDown(self):
        self.tmp.cleanup()

    def load(self):
        _, path = board.find_ticket(self.root, "1")
        return board.load_ticket(path)

    # Parsing

    def test_old_header_parses_to_the_same_comment_as_before(self):
        t = board.parse_ticket(SAMPLE)
        c = t.comments[0]
        self.assertEqual((c.by, c.at, c.body), ("claude", "2026-09-04T14:10:00Z", "Fixed in a1b2c3d."))
        self.assertIsNone(c.to)
        self.assertFalse(c.ask)
        self.assertEqual(c.re, [])
        self.assertIsNone(c.commit)

    def test_old_author_containing_the_separator_still_parses(self):
        t = board.parse_ticket(OLD_AUTHOR_WITH_SEPARATOR)
        self.assertEqual(t.comments[0].by, "odd · name")
        self.assertEqual(t.comments[0].at, "2026-09-04T14:10:00Z")
        self.assertEqual(t.comments[0].body, "hello")

    def test_each_trailer_round_trips_alone_and_together(self):
        cases = [
            dict(to="codex"),
            dict(ask=True, to="codex"),
            dict(re=[1]),
            dict(re=[1, 3, 7]),
            dict(commit="a1b2c3d"),
            dict(to="codex", ask=True, re=[2, 5], commit="a1b2c3d"),
        ]
        for fields in cases:
            c = board.Comment("claude", "2026-09-05T00:00:00Z", "hi", **fields)
            text = '---\nid: "001"\ntitle: T\ncreated: 2026-09-05T00:00:00Z\n---\n\n%s\nhi\n' % board.render_comment_header(c)
            self.assertEqual(board.parse_ticket(text).comments[0], c, fields)

    def test_header_is_written_in_fixed_order(self):
        c = board.Comment("claude", "2026-09-05T00:00:00Z", "hi",
                          to="codex", ask=True, re=[2, 5], commit="a1b2c3d")
        self.assertEqual(board.render_comment_header(c),
                         "## comment — claude · 2026-09-05T00:00:00Z · to codex · ask · re 2,5 · commit a1b2c3d")

    def test_unknown_trailer_is_ignored(self):
        text = '---\nid: "001"\ntitle: T\ncreated: 2026-09-05T00:00:00Z\n---\n\n## comment — claude · 2026-09-05T00:00:00Z · to codex · priority high\nhi\n'
        c = board.parse_ticket(text).comments[0]
        self.assertEqual(c.to, "codex")
        self.assertEqual(c.body, "hi")

    def test_malformed_re_counts_as_absent(self):
        text = '---\nid: "001"\ntitle: T\ncreated: 2026-09-05T00:00:00Z\n---\n\n## comment — claude · 2026-09-05T00:00:00Z · re one,2\nhi\n'
        self.assertEqual(board.parse_ticket(text).comments[0].re, [])

    def test_duplicate_trailer_keeps_the_first(self):
        text = '---\nid: "001"\ntitle: T\ncreated: 2026-09-05T00:00:00Z\n---\n\n## comment — claude · 2026-09-05T00:00:00Z · to codex · to human\nhi\n'
        self.assertEqual(board.parse_ticket(text).comments[0].to, "codex")

    def test_a_line_that_only_looks_like_a_header_is_body(self):
        text = '---\nid: "001"\ntitle: T\ncreated: 2026-09-05T00:00:00Z\n---\n\n## comment — claude · 2026-09-05T00:00:00Z\nhi\n## comment — not a timestamp here\nstill body\n'
        t = board.parse_ticket(text)
        self.assertEqual(len(t.comments), 1)
        self.assertIn("still body", t.comments[0].body)

    # Writing

    def test_add_comment_writes_trailers_and_returns_the_number(self):
        n1 = board.add_comment(self.root, "1", "please review", "claude", to="codex", ask=True, commit="abc123")
        n2 = board.add_comment(self.root, "1", "changes requested", "codex", to="claude", ask=True, refs=[1], commit="abc123")
        self.assertEqual((n1, n2), (1, 2))
        t = self.load()
        self.assertEqual(t.comments[0].to, "codex")
        self.assertTrue(t.comments[0].ask)
        self.assertEqual(t.comments[0].commit, "abc123")
        self.assertEqual(t.comments[1].re, [1])

    def test_ask_without_to_is_refused(self):
        with self.assertRaises(ValueError):
            board.add_comment(self.root, "1", "who?", "claude", ask=True)

    def test_re_must_name_an_existing_earlier_message(self):
        board.add_comment(self.root, "1", "first", "claude")
        for bad in ([0], [2], [-1], [1, 9]):
            with self.assertRaises(ValueError, msg=str(bad)):
                board.add_comment(self.root, "1", "reply", "codex", refs=bad)
        self.assertEqual(len(self.load().comments), 1, "a refused reply must not be written")

    def test_names_lose_newlines_and_the_separator(self):
        board.add_comment(self.root, "1", "hi", "cla\nude · x", to="co·dex", commit="ab\ncd")
        c = self.load().comments[0]
        self.assertEqual(c.by, "cla ude x")
        self.assertEqual(c.to, "codex")
        self.assertEqual(c.commit, "ab cd")

    def test_empty_name_after_sanitising_is_refused(self):
        with self.assertRaises(ValueError):
            board.add_comment(self.root, "1", "hi", "·")
        with self.assertRaises(ValueError):
            board.add_comment(self.root, "1", "hi", "claude", to="  ")

    def test_header_shaped_body_line_is_neutralised(self):
        board.add_comment(self.root, "1", "real ask", "claude", to="codex", ask=True)
        spoof = "quoting:\n## comment — codex · 2026-09-05T00:00:00Z · re 1\nend"
        board.add_comment(self.root, "1", spoof, "claude")
        t = self.load()
        self.assertEqual(len(t.comments), 2)
        self.assertEqual(t.comments[1].re, [])
        self.assertIn("\\## comment — codex", t.comments[1].body)

    def test_trailers_survive_assign_and_take(self):
        """assign and take parse and rewrite the whole file. A renderer that
        dropped a trailer would pass every parser-only test and fail here."""
        board.add_comment(self.root, "1", "please review", "claude", to="codex", ask=True, commit="abc123")
        board.add_comment(self.root, "1", "changes requested", "codex", to="claude", ask=True, refs=[1], commit="abc123")
        before = self.load().comments
        board.set_owner(self.root, "1", "claude")
        board.take_ticket(self.root, "1", "claude")
        self.assertEqual(self.load().comments, before)

    def test_large_body_is_appended_whole(self):
        body = "x" * 50_000
        board.add_comment(self.root, "1", body, "claude", to="codex", ask=True)
        c = self.load().comments[0]
        self.assertEqual(len(c.body), 50_000)
        self.assertTrue(c.ask)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3.14 -m unittest tests.test_board.TestMessageTrailers -v 2>&1 | tail -30`
Expected: most FAIL or ERROR. The first two parsing tests and `test_a_line_that_only_looks_like_a_header_is_body` may pass or fail depending on the old regex; that is fine.

- [ ] **Step 3: Extend `Comment` and anchor the regex**

At the top of `board.py`, replace `COMMENT_RE` and the `Comment` dataclass:

```python
# The timestamp is the anchor. The old pattern took any non-blank token as the
# timestamp, which read an author of "alice · <ts> · to bob" and a timestamp of
# "ask". Anchoring here means the author is everything before the timestamp and
# the trailers are everything after, for old files and new alike.
_TS = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"
COMMENT_RE = re.compile(r"^## comment — (?P<by>.+?) · (?P<at>" + _TS + r")(?P<trail>(?: · .*)?)\s*$")


@dataclass
class Comment:
    by: str
    at: str
    body: str
    to: str | None = None
    ask: bool = False
    re: list[int] = field(default_factory=list)
    commit: str | None = None
```

- [ ] **Step 4: Parse and render trailers**

Directly under the `Ticket` dataclass, add:

```python
def _parse_trailers(trail: str) -> tuple[str | None, bool, list[int], str | None]:
    """Read ` · to x · ask · re 1,2 · commit abc`. Unknown keys are ignored so
    a newer writer cannot make a file unreadable; a malformed known key counts
    as absent, never as something else; a duplicate keeps its first value."""
    to: str | None = None
    ask = False
    refs: list[int] = []
    commit: str | None = None
    seen: set[str] = set()
    for part in trail.split(" · "):
        part = part.strip()
        if not part:
            continue
        key, _, value = part.partition(" ")
        value = value.strip()
        if key in seen:
            continue
        if key == "to" and value:
            seen.add(key)
            to = value
        elif key == "ask" and not value:
            seen.add(key)
            ask = True
        elif key == "re":
            seen.add(key)
            nums: list[int] = []
            for tok in value.split(","):
                tok = tok.strip()
                if not tok.isdigit() or int(tok) < 1:
                    nums = []
                    break
                nums.append(int(tok))
            refs = nums
        elif key == "commit" and value:
            seen.add(key)
            commit = value
    return to, ask, refs, commit


def render_comment_header(c: Comment) -> str:
    parts = ["## comment — %s · %s" % (c.by, c.at)]
    if c.to:
        parts.append("to %s" % c.to)
    if c.ask:
        parts.append("ask")
    if c.re:
        parts.append("re %s" % ",".join(str(n) for n in c.re))
    if c.commit:
        parts.append("commit %s" % c.commit)
    return " · ".join(parts)
```

In `parse_ticket`, the loop currently stores `current = (match.group("by"), match.group("at"))` and builds `Comment(current[0], current[1], ...)` in two places. Change it so the header match is kept and the comment is built from it. Replace the body of `parse_ticket` from `desc: list[str] = []` down to the `return Ticket(` line with:

```python
    desc: list[str] = []
    comments: list[Comment] = []
    current: re.Match | None = None
    buf: list[str] = []

    def flush() -> None:
        to, ask, refs, commit = _parse_trailers(current.group("trail"))
        comments.append(Comment(current.group("by"), current.group("at"),
                                "\n".join(buf).strip(), to, ask, refs, commit))

    for line in body.split("\n"):
        match = COMMENT_RE.match(line)
        if match:
            if current is None:
                desc = buf
            else:
                flush()
            current = match
            buf = []
        else:
            buf.append(line)
    if current is None:
        desc = buf
    else:
        flush()
```

Leave the `return Ticket(...)` as it is.

In `render_ticket`, replace the comment loop:

```python
    for c in t.comments:
        text += "\n%s\n%s\n" % (render_comment_header(c), c.body.strip())
```

- [ ] **Step 5: Add `sanitize_name` and `neutralise_body`**

Directly after `sanitize_scalar`:

```python
def sanitize_name(value: str) -> str:
    """A header scalar: one line, and never the separator, so a name cannot end
    the header early or start a trailer of its own."""
    return sanitize_scalar(value.replace("·", ""))


def neutralise_body(body: str) -> str:
    """A body line shaped like a header would parse as a new message and, with
    `re`, could clear a real request. A leading backslash defeats the anchored
    parser, survives the strip the parser and renderer apply to bodies, and
    renders as literal text in markdown."""
    return "\n".join("\\" + line if COMMENT_RE.match(line) else line
                     for line in body.split("\n"))
```

- [ ] **Step 6: Rewrite `add_comment` around a locked helper with one write loop**

Replace `add_comment`:

```python
def _prepare_comment(body: str, by: str, to: str | None, ask: bool,
                     refs: list[int] | None, commit: str | None) -> Comment:
    """Everything about a new message that can be checked without the lock."""
    by = sanitize_name(by)
    if not by:
        raise ValueError("--by must name who is writing")
    if to is not None:
        to = sanitize_name(to) or None
        if to is None:
            raise ValueError("--to must name a recipient")
    if commit is not None:
        commit = sanitize_name(commit) or None
    if ask and not to:
        raise ValueError("--ask needs --to: who should answer?")
    refs = sorted(set(refs or []))
    for n in refs:
        if n < 1:
            raise ValueError("--re %d: message numbers start at 1" % n)
    return Comment(by=by, at=utc_now(), body=neutralise_body(body.strip()),
                   to=to, ask=ask, re=refs, commit=commit)


def _append_comment_locked(path: str, comment: Comment) -> int:
    """Append one message and return its number. Caller holds the board lock.

    `re` is checked here, against the file as it is under the lock, so a
    reference can only point at a message that already exists. The append is a
    single write loop on an O_APPEND descriptor, not a buffered text write that
    may land in pieces, so a reader that takes the lock never sees a header
    without its body.
    """
    existing = len(load_ticket(path).comments)
    for n in comment.re:
        if n > existing:
            raise ValueError("--re %d: this file has %d message%s"
                             % (n, existing, "" if existing == 1 else "s"))
    data = ("\n%s\n%s\n" % (render_comment_header(comment), comment.body)).encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
    finally:
        os.close(fd)
    return existing + 1


def add_comment(root: str, tid: str, body: str, by: str, to: str | None = None,
                ask: bool = False, refs: list[int] | None = None,
                commit: str | None = None) -> int:
    """Append a message to a ticket or thread and return its number.

    Everything under the lock, as before: a writer holding the lock can read the
    file, an unlocked append can land, and the writer's rewrite then erases it.
    A lock-free append can also recreate a ticket at its old path after a move.
    """
    comment = _prepare_comment(body, by, to, ask, refs, commit)
    with board_lock(root):
        _, path = find_ticket(root, tid)
        return _append_comment_locked(path, comment)
```

- [ ] **Step 7: Run the new tests and the whole suite**

Run: `python3.14 -m unittest tests.test_board.TestMessageTrailers -v 2>&1 | tail -25`
Expected: 17 tests PASS.

Run: `python3.14 -m unittest discover -s tests -v 2>&1 | tail -5`
Expected: `OK`. If `TestParsing.test_round_trip_is_stable` fails, the renderer and parser disagree on a default; fix the renderer, never the test.

- [ ] **Step 8: Correct the spec's neutralising sentence**

In `docs/superpowers/specs/2026-09-05-threads-and-inbox-design.md`, find the paragraph beginning `**Body lines that look like a header are neutralised on write.**` and replace it so it reads:

```
**Body lines that look like a header are neutralised on write.** Any body line
matching the header pattern gets one leading backslash, which markdown renders
as literal text, the anchored parser no longer matches, and the `strip()` that
the parser and renderer apply to bodies leaves in place. Until now a spoofed
header only mis-rendered a comment; with `re` it could clear a real request, so
this is in scope. Existing files are not rewritten; ticket 001 on this repo's
board tracks the residue.
```

- [ ] **Step 9: Commit**

```bash
git add board.py tests/test_board.py docs/superpowers/specs/2026-09-05-threads-and-inbox-design.md
git commit -m "feat: message trailers — to, ask, re, commit — on every comment

The header parser now anchors on the timestamp so old authors and new
trailers cannot be confused. Names are sanitised on write, header-shaped
body lines are escaped, re is validated under the lock, and the append is
one write loop so a locked reader never sees a header without its body."
```

---

### Task 3: Threads

A thread is a ticket-shaped file under `.agent-board/threads/`. It shares the allocator, the parser, and the lookup, and refuses the three operations that need a column.

**Files:**
- Modify: `board.py` — `Ticket` dataclass, `parse_ticket`, `render_ticket`, `init_board`, `_all_ticket_paths`, `_create_locked`, `find_ticket`, `_move_locked`, `take_ticket`, `set_owner`, `ticket_to_dict`; new `create_thread`, `list_threads`, `is_thread`
- Test: `tests/test_board.py` — new class `TestThreads`

**Interfaces:**
- Consumes: `_create_locked`, `_prepare_comment`, `_append_comment_locked`, `board_lock`, `find_ticket`, `validate_id`.
- Produces:
  - `THREADS_DIR = "threads"`; `is_thread(column: str) -> bool`.
  - `Ticket.ticket: str | None` — the optional link to a ticket, written as `ticket: "009"`.
  - `_create_locked(..., owner=None, ticket_ref: str | None = None)` — one new keyword.
  - `create_thread(root, title, body, by, to=None, ask=False, commit=None, ticket=None) -> str` — returns the id.
  - `list_threads(root) -> list[Ticket]` — newest activity first.
  - `find_ticket` returns `(THREADS_DIR, path)` for a thread.
  - `ticket_to_dict` adds `kind` (`"ticket"` or `"thread"`) and `n` on each comment.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_board.py`:

```python
class TestThreads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = board.init_board(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_creates_threads_dir(self):
        self.assertTrue(os.path.isdir(os.path.join(self.root, board.THREADS_DIR)))

    def test_create_thread_writes_message_one_and_shares_the_id_space(self):
        board.create_ticket(self.root, "a ticket")
        tid = board.create_thread(self.root, "Design A vs B", "Opening.\n\nDetails.", "claude", to="codex", ask=True, commit="abc123")
        self.assertEqual(tid, "002")
        col, path = board.find_ticket(self.root, tid)
        self.assertEqual(col, board.THREADS_DIR)
        self.assertTrue(path.endswith("002-design-a-vs-b.md"))
        t = board.load_ticket(path)
        self.assertEqual(t.title, "Design A vs B")
        self.assertEqual(t.description, "")
        self.assertEqual(len(t.comments), 1)
        self.assertEqual(t.comments[0].body, "Opening.\n\nDetails.")
        self.assertEqual(t.comments[0].to, "codex")
        self.assertTrue(t.comments[0].ask)
        self.assertIsNone(t.ticket)
        self.assertEqual(board.create_ticket(self.root, "next ticket"), "003")

    def test_thread_may_link_a_ticket(self):
        board.create_ticket(self.root, "the work")
        tid = board.create_thread(self.root, "about the work", "hi", "claude", ticket="1")
        _, path = board.find_ticket(self.root, tid)
        t = board.load_ticket(path)
        self.assertEqual(t.ticket, "001")
        with open(path, encoding="utf-8") as fh:
            self.assertIn('ticket: "001"', fh.read())
        self.assertEqual(board.parse_ticket(board.render_ticket(t)), t)

    def test_thread_link_to_missing_ticket_is_refused(self):
        with self.assertRaises(KeyError):
            board.create_thread(self.root, "t", "hi", "claude", ticket="42")

    def test_thread_opening_ask_needs_a_recipient(self):
        with self.assertRaises(ValueError):
            board.create_thread(self.root, "t", "hi", "claude", ask=True)
        self.assertEqual(os.listdir(os.path.join(self.root, board.THREADS_DIR)), [])

    def test_comment_on_a_thread_works(self):
        tid = board.create_thread(self.root, "t", "hi", "claude", to="codex", ask=True)
        n = board.add_comment(self.root, tid, "answer", "codex", refs=[1])
        self.assertEqual(n, 2)

    def test_move_take_assign_refuse_a_thread(self):
        tid = board.create_thread(self.root, "t", "hi", "claude")
        with self.assertRaises(ValueError):
            board.move_ticket(self.root, tid, "review")
        with self.assertRaises(ValueError):
            board.take_ticket(self.root, tid, "codex")
        with self.assertRaises(ValueError):
            board.set_owner(self.root, tid, "codex")
        col, path = board.find_ticket(self.root, tid)
        self.assertEqual(col, board.THREADS_DIR)
        self.assertIsNone(board.load_ticket(path).owner)
        for c in board.COLUMNS:
            self.assertEqual(os.listdir(os.path.join(self.root, c)), [], c)

    def test_list_threads_newest_activity_first(self):
        a = board.create_thread(self.root, "older", "hi", "claude")
        b = board.create_thread(self.root, "newer", "hi", "claude")
        # Same second is likely; force distinct activity by editing the file.
        _, path = board.find_ticket(self.root, a)
        t = board.load_ticket(path)
        t.comments[0].at = "2030-01-01T00:00:00Z"
        board.atomic_write(path, board.render_ticket(t))
        self.assertEqual([t.id for t in board.list_threads(self.root)], [a, b])

    def test_list_tickets_excludes_threads(self):
        board.create_thread(self.root, "t", "hi", "claude")
        self.assertEqual(board.list_tickets(self.root), [])

    def test_to_dict_marks_kind_and_numbers_messages(self):
        tid = board.create_thread(self.root, "t", "hi", "claude")
        board.add_comment(self.root, tid, "second", "codex")
        col, path = board.find_ticket(self.root, tid)
        d = board.ticket_to_dict(col, board.load_ticket(path))
        self.assertEqual(d["kind"], "thread")
        self.assertEqual(d["column"], board.THREADS_DIR)
        self.assertEqual([c["n"] for c in d["comments"]], [1, 2])
        board.create_ticket(self.root, "k")
        col, path = board.find_ticket(self.root, "2")
        self.assertEqual(board.ticket_to_dict(col, board.load_ticket(path))["kind"], "ticket")

    def test_board_without_threads_dir_still_works(self):
        board.create_ticket(self.root, "old board")
        os.rmdir(os.path.join(self.root, board.THREADS_DIR))
        self.assertEqual(len(board.list_tickets(self.root)), 1)
        self.assertEqual(board.list_threads(self.root), [])
        board.find_ticket(self.root, "1")
        board.render_board_html(self.root)
        tid = board.create_thread(self.root, "first thread", "hi", "claude")
        self.assertEqual(tid, "002")

    def test_concurrent_thread_and_ticket_creation_never_share_an_id(self):
        mod = os.path.dirname(os.path.abspath(board.__file__))
        code = (
            "import sys; sys.path.insert(0, %r); import board\n"
            "i = int(sys.argv[1])\n"
            "print(board.create_thread(%r, 'thread %%d' %% i, 'hi', 'claude') if i %% 2"
            " else board.create_ticket(%r, 'ticket %%d' %% i))"
        ) % (mod, self.root, self.root)
        procs = [subprocess.Popen([sys.executable, "-c", code, str(i)],
                                  stdout=subprocess.PIPE, text=True) for i in range(8)]
        ids = [p.communicate()[0].strip() for p in procs]
        self.assertEqual(len(set(ids)), 8, ids)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3.14 -m unittest tests.test_board.TestThreads -v 2>&1 | tail -20`
Expected: ERROR on `board.THREADS_DIR` and `board.create_thread` not existing.

- [ ] **Step 3: Add the constant, the `ticket` field, and parse/render it**

Near `COLUMNS` at the top of `board.py`:

```python
COLUMNS = ("todo", "doing", "review", "blocked", "done")
THREADS_DIR = "threads"
```

Add to the `Ticket` dataclass, after `branch`:

```python
    ticket: str | None = None
```

In `parse_ticket`'s `return Ticket(...)`, add after the `branch=` line:

```python
        ticket=meta.get("ticket") or None,
```

In `render_ticket`, after the `if t.branch:` block:

```python
    if t.ticket:
        out.append('ticket: "%s"' % t.ticket)
```

Add after `validate_column`:

```python
def is_thread(column: str) -> bool:
    return column == THREADS_DIR
```

- [ ] **Step 4: Teach init, the scan, and lookup about `threads/`**

In `init_board`, after the loop that creates the columns:

```python
    os.makedirs(os.path.join(root, THREADS_DIR), exist_ok=True)
```

Replace `_all_ticket_paths`:

```python
def _all_ticket_paths(root: str) -> list[str]:
    paths = list(glob.glob(os.path.join(root, "*.md")))
    for directory in COLUMNS + (THREADS_DIR,):
        paths.extend(glob.glob(os.path.join(root, directory, "*.md")))
    return paths
```

In `find_ticket`, change the loop header `for col in COLUMNS:` to:

```python
    for col in COLUMNS + (THREADS_DIR,):
```

- [ ] **Step 5: Thread creation through the same locked allocator**

Change `_create_locked`'s signature and the `Ticket(...)` construction inside it:

```python
def _create_locked(root: str, title: str, description: str, dest_dir: str,
                   owner: str | None = None, ticket_ref: str | None = None) -> tuple[str, str]:
```

```python
            ticket = Ticket(id=tid, title=title, created=utc_now(),
                            description=description, owner=owner, ticket=ticket_ref)
```

Add after `create_ticket`:

```python
def create_thread(root: str, title: str, body: str, by: str, to: str | None = None,
                  ask: bool = False, commit: str | None = None,
                  ticket: str | None = None) -> str:
    """Start a conversation: a column-less file under threads/ whose first
    message is the opening post. One step, because two steps to start talking
    is the friction that stops it happening. Shares the id space with tickets
    so `board show 12` never has to ask which kind 12 is."""
    comment = _prepare_comment(body, by, to, ask, None, commit)
    ticket_ref = validate_id(ticket) if ticket else None
    with board_lock(root):
        if ticket_ref is not None:
            find_ticket(root, ticket_ref)      # KeyError if it does not exist
        tid, path = _create_locked(root, title, "", THREADS_DIR, ticket_ref=ticket_ref)
        _append_comment_locked(path, comment)
    return tid


def list_threads(root: str) -> list[Ticket]:
    """Every thread, newest activity first. Activity is the last message's
    time, or creation for a thread with none, which cannot happen through the
    CLI but can through a hand-made file."""
    rows = [load_ticket(p) for p in sorted(glob.glob(os.path.join(root, THREADS_DIR, "*.md")))]
    rows.sort(key=lambda t: (t.comments[-1].at if t.comments else t.created, int(t.id)),
              reverse=True)
    return rows
```

- [ ] **Step 6: Refuse a thread inside the shared mutators**

Add after `is_thread`:

```python
def _refuse_thread(column: str, tid: str, verb: str) -> None:
    if is_thread(column):
        raise ValueError("%s is a thread; threads have no column and no owner, so it cannot be %s"
                         % (validate_id(tid), verb))
```

In `_move_locked`, the line `_, path = find_ticket(root, tid)` becomes:

```python
    col, path = find_ticket(root, tid)
    _refuse_thread(col, tid, "moved")
```

In `take_ticket`, directly after `col, path = find_ticket(root, tid)`:

```python
        _refuse_thread(col, tid, "taken")
```

In `set_owner`, the line `_, path = find_ticket(root, tid)` becomes:

```python
        col, path = find_ticket(root, tid)
        _refuse_thread(col, tid, "assigned")
```

- [ ] **Step 7: `ticket_to_dict` gains `kind` and message numbers**

Replace `ticket_to_dict`:

```python
def ticket_to_dict(column: str, t: Ticket) -> dict:
    data = asdict(t)
    data["column"] = column
    data["kind"] = "thread" if is_thread(column) else "ticket"
    for n, c in enumerate(data["comments"], start=1):
        c["n"] = n
    return data
```

- [ ] **Step 8: Run the new tests and the whole suite**

Run: `python3.14 -m unittest tests.test_board.TestThreads -v 2>&1 | tail -20`
Expected: 12 tests PASS.

Run: `python3.14 -m unittest discover -s tests -v 2>&1 | tail -5`
Expected: `OK`.

- [ ] **Step 9: Commit**

```bash
git add board.py tests/test_board.py
git commit -m "feat: threads — column-less conversations under threads/

A thread is a ticket-shaped file whose opening post is message 1. It
shares the allocator and the lookup, may link a ticket, and refuses move,
take and assign inside the shared mutators, before anything is written."
```

---

### Task 4: Pending, inbox, and the CLI

The inbox rule as one function, the inbox query over tickets and threads, `show --last`, bodies from a file or stdin, and the new CLI commands. Reads that compute pending take the lock.

**Files:**
- Modify: `board.py` — new query section after `watch_once`; `main` (parsers and dispatch); imports (`replace` from dataclasses)
- Test: `tests/test_board.py` — new classes `TestPending` and `TestConversationCLI`

**Interfaces:**
- Consumes: `Ticket`, `Comment`, `list_tickets`, `list_threads`, `find_ticket`, `load_ticket`, `board_lock`, `create_thread`, `add_comment`, `render_comment_header`, `ticket_to_dict`, `THREADS_DIR`, `is_thread`.
- Produces:
  - `pending_asks(t: Ticket, name: str | None = None) -> list[tuple[int, Comment]]`
  - `_all_items(root) -> list[tuple[str, Ticket]]` — tickets in all columns then threads. No lock; callers take it.
  - `_first_line(body: str) -> str`
  - `answered_unseen(t: Ticket, name: str) -> list[tuple[int, Comment, int, Comment]]` — `(ask_n, ask, answer_n, answer)` for each ask `name` posted that a later message answers with `re`, where `name` has posted nothing in the file after that answer.
  - `inbox_rows(root, name: str | None = None) -> list[dict]` — takes the lock. Row keys: `id, kind, column, title, n, by, to, at, commit, summary, state, asked`. `state` is `"awaiting"` or `"answered"`; `asked` is the asker's message number on answered rows and `None` otherwise. Answered rows appear only when a name is given.
  - CLI: `thread`, `threads`, `inbox`, `comment` flags, `show --last`, `--body-file`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_board.py`:

```python
class TestPending(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = board.init_board(self.tmp.name)
        self.tid = board.create_thread(self.root, "t", "opening", "claude")

    def tearDown(self):
        self.tmp.cleanup()

    def thread(self):
        _, path = board.find_ticket(self.root, self.tid)
        return board.load_ticket(path)

    def numbers(self, name=None):
        return [n for n, _ in board.pending_asks(self.thread(), name)]

    def test_unanswered_ask_is_pending_for_its_recipient_only(self):
        board.add_comment(self.root, self.tid, "q", "claude", to="codex", ask=True)
        self.assertEqual(self.numbers("codex"), [2])
        self.assertEqual(self.numbers("claude"), [])
        self.assertEqual(self.numbers(), [2])

    def test_a_message_without_ask_is_never_pending(self):
        board.add_comment(self.root, self.tid, "fyi", "claude", to="codex")
        self.assertEqual(self.numbers(), [])

    def test_reply_by_re_clears_it(self):
        board.add_comment(self.root, self.tid, "q", "claude", to="codex", ask=True)
        board.add_comment(self.root, self.tid, "a", "codex", to="claude", refs=[2])
        self.assertEqual(self.numbers(), [])

    def test_reply_that_asks_again_is_pending_for_the_other_party(self):
        board.add_comment(self.root, self.tid, "review please", "claude", to="codex", ask=True)
        board.add_comment(self.root, self.tid, "changes requested", "codex", to="claude", ask=True, refs=[2])
        self.assertEqual(self.numbers("codex"), [])
        self.assertEqual(self.numbers("claude"), [3])

    def test_re_list_clears_several_at_once(self):
        for i in range(3):
            board.add_comment(self.root, self.tid, "task %d" % i, "claude", to="codex", ask=True)
        board.add_comment(self.root, self.tid, "whole branch supersedes", "claude", to="codex", ask=True, refs=[2, 3, 4])
        self.assertEqual(self.numbers("codex"), [5])

    def test_askers_own_re_clears_it(self):
        board.add_comment(self.root, self.tid, "q", "claude", to="codex", ask=True)
        board.add_comment(self.root, self.tid, "never mind", "claude", refs=[2])
        self.assertEqual(self.numbers(), [])

    def test_name_matching_ignores_case(self):
        board.add_comment(self.root, self.tid, "q", "claude", to="Codex", ask=True)
        self.assertEqual(self.numbers("codex"), [2])
        self.assertEqual(self.numbers("CODEX"), [2])

    def test_dangling_or_forward_re_changes_nothing(self):
        board.add_comment(self.root, self.tid, "q", "claude", to="codex", ask=True)
        _, path = board.find_ticket(self.root, self.tid)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n## comment — codex · 2026-09-05T00:00:00Z · re 9\nlate\n")
            fh.write("\n## comment — codex · 2026-09-05T00:00:01Z · re 4\nself\n")
        self.assertEqual(self.numbers("codex"), [2])

    def test_pending_survives_take_on_a_ticket(self):
        tid = board.create_ticket(self.root, "work")
        board.add_comment(self.root, tid, "review?", "claude", to="codex", ask=True)
        board.take_ticket(self.root, tid, "codex")
        _, path = board.find_ticket(self.root, tid)
        self.assertEqual([n for n, _ in board.pending_asks(board.load_ticket(path), "codex")], [1])

    def test_inbox_rows_span_tickets_and_threads_newest_first(self):
        tid = board.create_ticket(self.root, "work")
        board.add_comment(self.root, tid, "on the ticket", "claude", to="codex", ask=True)
        board.add_comment(self.root, self.tid, "in the thread\nmore", "claude", to="codex", ask=True, commit="abc")
        rows = board.inbox_rows(self.root, "codex")
        self.assertEqual({r["kind"] for r in rows}, {"ticket", "thread"})
        thread_row = next(r for r in rows if r["kind"] == "thread")
        self.assertEqual(thread_row["summary"], "in the thread")
        self.assertEqual(thread_row["commit"], "abc")
        self.assertEqual(thread_row["n"], 2)
        self.assertEqual(board.inbox_rows(self.root, "nobody"), [])
        self.assertEqual(len(board.inbox_rows(self.root)), 2)
        for r in rows:
            self.assertEqual(set(r), {"id", "kind", "column", "title", "n", "by", "to", "at",
                                      "commit", "summary", "state", "asked"})
            self.assertEqual(r["state"], "awaiting")

    def test_answered_ask_is_unseen_until_the_asker_posts_again(self):
        """An approval is a reply with no ask. Without this the engineer who asked
        for review sees an empty inbox and cannot tell acceptance from silence."""
        board.add_comment(self.root, self.tid, "review?", "claude", to="codex", ask=True)
        self.assertEqual(board.answered_unseen(self.thread(), "claude"), [])
        board.add_comment(self.root, self.tid, "approved", "codex", to="claude", refs=[2])
        rows = board.answered_unseen(self.thread(), "claude")
        self.assertEqual([(a, n) for a, _, n, _ in rows], [(2, 3)])
        self.assertEqual(board.answered_unseen(self.thread(), "codex"), [])
        board.add_comment(self.root, self.tid, "thanks", "claude")
        self.assertEqual(board.answered_unseen(self.thread(), "claude"), [])

    def test_inbox_rows_carry_answered_rows_only_for_a_name(self):
        board.add_comment(self.root, self.tid, "review?", "claude", to="codex", ask=True)
        board.add_comment(self.root, self.tid, "approved\ndetails", "codex", to="claude", refs=[2])
        mine = board.inbox_rows(self.root, "claude")
        self.assertEqual([r["state"] for r in mine], ["answered"])
        self.assertEqual((mine[0]["asked"], mine[0]["n"], mine[0]["by"], mine[0]["summary"]),
                         (2, 3, "codex", "approved"))
        self.assertEqual(board.inbox_rows(self.root, "codex"), [])
        self.assertEqual(board.inbox_rows(self.root), [])


class TestConversationCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.run_cli("init", "--no-agents")

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def run_cli(self, *args, stdin=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            if stdin is None:
                code = board.main(list(args))
            else:
                with mock.patch.object(sys, "stdin", io.StringIO(stdin)):
                    code = board.main(list(args))
        return code, out.getvalue()

    def test_thread_then_inbox_then_reply(self):
        code, out = self.run_cli("thread", "Design", "Attack the filter first", "--by", "claude", "--to", "codex", "--ask", "--commit", "abc")
        self.assertEqual(code, 0)
        tid = out.strip()
        self.assertEqual(tid, "001")
        code, out = self.run_cli("inbox", "codex")
        self.assertEqual(code, 0)
        self.assertIn("001", out)
        self.assertIn("Attack the filter first", out)
        self.assertIn("abc", out)
        code, out = self.run_cli("comment", tid, "Approved", "--by", "codex", "--to", "claude", "--re", "1")
        self.assertEqual(code, 0)
        self.assertIn("2", out)
        _, out = self.run_cli("inbox", "codex")
        self.assertIn("nothing pending", out)
        _, out = self.run_cli("inbox", "claude")
        self.assertIn("ANSWERED", out)
        self.assertIn("Approved", out)
        _, out = self.run_cli("inbox", "--json")
        self.assertEqual(json.loads(out), [])

    def test_inbox_without_a_name_is_the_human_view(self):
        self.run_cli("thread", "A", "q1", "--by", "claude", "--to", "codex", "--ask")
        self.run_cli("new", "work")
        self.run_cli("comment", "2", "q2", "--by", "codex", "--to", "human", "--ask")
        _, out = self.run_cli("inbox", "--json")
        rows = json.loads(out)
        self.assertEqual(sorted(r["to"] for r in rows), ["codex", "human"])

    def test_threads_lists_with_pending_count(self):
        self.run_cli("thread", "Quiet", "hi", "--by", "claude")
        self.run_cli("thread", "Loud", "q", "--by", "claude", "--to", "codex", "--ask")
        code, out = self.run_cli("threads")
        self.assertEqual(code, 0)
        self.assertIn("Loud", out)
        self.assertIn("1 pending", out)
        _, out = self.run_cli("threads", "--json")
        rows = json.loads(out)
        self.assertEqual([r["title"] for r in rows][:1], ["Loud"])
        self.assertEqual({r["pending"] for r in rows}, {0, 1})

    def test_show_last_keeps_numbers(self):
        self.run_cli("thread", "T", "one", "--by", "claude")
        for body in ("two", "three", "four"):
            self.run_cli("comment", "1", body, "--by", "codex")
        code, out = self.run_cli("show", "1", "--last", "2")
        self.assertEqual(code, 0)
        self.assertIn("2 earlier messages omitted", out)
        self.assertIn("showing 3-4 of 4", out)
        self.assertNotIn("\ntwo\n", out)
        self.assertIn("four", out)
        _, out = self.run_cli("show", "1", "--last", "2", "--json")
        self.assertEqual([c["n"] for c in json.loads(out)["comments"]], [3, 4])

    def test_body_from_file_and_stdin(self):
        with open("body.md", "w", encoding="utf-8") as fh:
            fh.write("Summary line\n\nLong essay.\n")
        code, _ = self.run_cli("thread", "T", "--by", "claude", "--body-file", "body.md")
        self.assertEqual(code, 0)
        code, _ = self.run_cli("comment", "1", "--by", "codex", "--body-file", "-", stdin="from stdin\n")
        self.assertEqual(code, 0)
        _, out = self.run_cli("show", "1", "--json")
        bodies = [c["body"] for c in json.loads(out)["comments"]]
        self.assertEqual(bodies, ["Summary line\n\nLong essay.", "from stdin"])

    def test_missing_body_and_both_bodies_fail_cleanly(self):
        code, _ = self.run_cli("thread", "T", "--by", "claude")
        self.assertEqual(code, 2)
        with open("b.md", "w") as fh:
            fh.write("x")
        code, _ = self.run_cli("thread", "T", "inline", "--by", "claude", "--body-file", "b.md")
        self.assertEqual(code, 2)

    def test_bad_re_and_ask_without_to_exit_two(self):
        self.run_cli("thread", "T", "one", "--by", "claude")
        code, _ = self.run_cli("comment", "1", "x", "--by", "codex", "--re", "one")
        self.assertEqual(code, 2)
        code, _ = self.run_cli("comment", "1", "x", "--by", "codex", "--re", "0")
        self.assertEqual(code, 2)
        code, _ = self.run_cli("comment", "1", "x", "--by", "codex", "--ask")
        self.assertEqual(code, 2)

    def test_move_of_a_thread_exits_two_with_a_plain_message(self):
        self.run_cli("thread", "T", "one", "--by", "claude")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code, _ = self.run_cli("move", "1", "review")
        self.assertEqual(code, 2)
        self.assertIn("thread", err.getvalue())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3.14 -m unittest tests.test_board.TestPending tests.test_board.TestConversationCLI -v 2>&1 | tail -25`
Expected: ERROR on `board.pending_asks` and `board.inbox_rows`; the CLI tests fail with argparse errors on unknown commands.

- [ ] **Step 3: The query section**

Add after `watch_once` in `board.py`:

```python
def pending_asks(t: Ticket, name: str | None = None) -> list[tuple[int, Comment]]:
    """The inbox rule, stated once: a message is pending when it carries `ask`,
    its `to` matches `name` (any case), and no later message in the same file
    lists it in `re`. Who spoke last is never consulted; the bridge's tool
    guessed from that and was patched twice after hiding a real request.
    A `re` naming a message at or after itself has no effect."""
    answered: set[int] = set()
    for i, c in enumerate(t.comments, start=1):
        answered.update(n for n in c.re if 1 <= n < i)
    want = name.lower() if name is not None else None
    rows = []
    for i, c in enumerate(t.comments, start=1):
        if not c.ask or not c.to or i in answered:
            continue
        if want is not None and c.to.lower() != want:
            continue
        rows.append((i, c))
    return rows


def answered_unseen(t: Ticket, name: str) -> list[tuple[int, Comment, int, Comment]]:
    """The inbox's second question: what came back to me. An ask `name` posted
    that a later message answers with `re`, where `name` has posted nothing in
    the file after that answer. Posting anything afterwards is the
    acknowledgement. Returns (ask_n, ask, answer_n, answer), latest answer."""
    me = name.lower()
    last_mine = max((i for i, c in enumerate(t.comments, start=1) if c.by.lower() == me),
                    default=0)
    rows = []
    for i, c in enumerate(t.comments, start=1):
        if not c.ask or c.by.lower() != me:
            continue
        answers = [(j, d) for j, d in enumerate(t.comments, start=1) if j > i and i in d.re]
        if answers and last_mine < answers[-1][0]:
            rows.append((i, c, answers[-1][0], answers[-1][1]))
    return rows


def _all_items(root: str) -> list[tuple[str, Ticket]]:
    """Every ticket and every thread. No lock here: callers that compute
    pending take the board lock once around this call."""
    items = list_tickets(root)
    items.extend((THREADS_DIR, t) for t in list_threads(root))
    return items


def _first_line(body: str) -> str:
    for line in body.split("\n"):
        if line.strip():
            return line.strip()
    return ""


def inbox_rows(root: str, name: str | None = None) -> list[dict]:
    """Pending asks across the whole board, newest first. With no name, every
    pending ask with its recipient: the human's view."""
    with board_lock(root):
        items = _all_items(root)
    def row(col, t, n, c, state, asked):
        return {
            "id": t.id,
            "kind": "thread" if is_thread(col) else "ticket",
            "column": col,
            "title": t.title,
            "n": n,
            "by": c.by,
            "to": c.to,
            "at": c.at,
            "commit": c.commit,
            "summary": _first_line(c.body),
            "state": state,
            "asked": asked,
        }

    rows = []
    for col, t in items:
        for n, c in pending_asks(t, name):
            rows.append(row(col, t, n, c, "awaiting", None))
        if name is not None:
            for ask_n, _, n, c in answered_unseen(t, name):
                rows.append(row(col, t, n, c, "answered", ask_n))
    rows.sort(key=lambda r: (r["state"] == "awaiting", r["at"], int(r["id"]), r["n"]), reverse=True)
    return rows


def _print_inbox(rows: list[dict]) -> None:
    if not rows:
        print("(nothing pending)")
        return
    for state, heading in (("awaiting", "AWAITING YOUR REPLY"),
                           ("answered", "ANSWERED, NOT YET SEEN BY YOU")):
        section = [r for r in rows if r["state"] == state]
        if not section:
            continue
        print("%s (%d)" % (heading, len(section)))
        for r in section:
            commit = "  %s" % r["commit"] if r["commit"] else ""
            asked = "  (your #%d)" % r["asked"] if r["asked"] else ""
            print("  %s  %-6s #%-3d %s to %s  %s%s%s\n        %s"
                  % (r["id"], r["kind"], r["n"], r["by"], r["to"],
                     r["at"].replace("T", " ").rstrip("Z"), commit, asked, r["summary"][:110]))
        print()


def _print_threads(rows: list[Ticket]) -> None:
    if not rows:
        print("(no threads)")
        return
    for t in rows:
        pend = len(pending_asks(t))
        last = t.comments[-1] if t.comments else None
        when = ("%s %s" % (last.at.replace("T", " ").rstrip("Z"), last.by)) if last else t.created
        print("%s  %-44.44s %3d msg%s  %-10s %s"
              % (t.id, t.title, len(t.comments), " " if len(t.comments) == 1 else "s",
                 ("%d pending" % pend) if pend else "-", when))
```

- [ ] **Step 4: CLI helpers for bodies and `re`**

Add before `main`:

```python
def _body_from_args(body: str | None, body_file: str | None) -> str:
    """The body comes from the argument or from a file; `-` means stdin. Bodies
    in the record are multi-kilobyte essays, and shell quoting is not the place
    for them."""
    if body_file:
        if body is not None:
            raise ValueError("give the body as an argument or with --body-file, not both")
        if body_file == "-":
            return sys.stdin.read()
        with open(body_file, encoding="utf-8") as fh:
            return fh.read()
    if body is None:
        raise ValueError("a body is required: pass it as an argument or with --body-file PATH (- for stdin)")
    return body


def _parse_refs(value: str | None) -> list[int]:
    if not value:
        return []
    refs = []
    for tok in value.split(","):
        tok = tok.strip()
        if not tok.isdigit():
            raise ValueError("--re expects message numbers like 3 or 1,2,3, not %r" % tok)
        refs.append(int(tok))
    return refs
```

Add `replace` to the dataclasses import at the top of the file:

```python
from dataclasses import asdict, dataclass, field, replace
```

- [ ] **Step 5: Parsers**

In `main`, replace the `show` and `comment` parsers and add three new ones. The final parser block for these five commands:

```python
    p = sub.add_parser("show")
    p.add_argument("id")
    p.add_argument("--last", type=int, default=None, metavar="N",
                   help="only the last N messages, numbered as in the file")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("comment", help="post a message on a ticket or thread")
    p.add_argument("id")
    p.add_argument("body", nargs="?", default=None)
    p.add_argument("--by", required=True)
    p.add_argument("--to", default=None, help="who this is addressed to")
    p.add_argument("--ask", action="store_true", help="a reply is expected (needs --to)")
    p.add_argument("--re", default=None, metavar="N[,N...]",
                   help="message numbers this answers")
    p.add_argument("--commit", default=None, help="the commit this is about; carried, never checked")
    p.add_argument("--body-file", default=None, metavar="PATH", help="read the body from PATH, or - for stdin")

    p = sub.add_parser("thread", help="start a conversation that is not a ticket")
    p.add_argument("title")
    p.add_argument("body", nargs="?", default=None)
    p.add_argument("--by", required=True)
    p.add_argument("--to", default=None)
    p.add_argument("--ask", action="store_true")
    p.add_argument("--commit", default=None)
    p.add_argument("--ticket", default=None, metavar="ID", help="the ticket this thread is about, if any")
    p.add_argument("--body-file", default=None, metavar="PATH")

    p = sub.add_parser("threads", help="list conversations, newest activity first")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("inbox", help="what is waiting on NAME; every pending ask with no NAME")
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("--json", action="store_true")
```

- [ ] **Step 6: Dispatch**

In `main`'s `try:` block, replace the `show` and `comment` branches and add the new ones:

```python
        elif args.cmd == "show":
            with board_lock(root):
                col, path = find_ticket(root, args.id)
                ticket = load_ticket(path)
            total = len(ticket.comments)
            if args.json:
                data = ticket_to_dict(col, ticket)
                if args.last is not None:
                    data["comments"] = data["comments"][-args.last:] if args.last > 0 else []
                print(json.dumps(data, indent=2))
            elif args.last is not None and args.last < total:
                shown = ticket.comments[-args.last:] if args.last > 0 else []
                print("[%s] %s" % (col, path))
                print(render_ticket(replace(ticket, comments=[])).rstrip("\n"))
                omitted = total - len(shown)
                print("\n(%d earlier message%s omitted; showing %d-%d of %d)"
                      % (omitted, "" if omitted == 1 else "s", omitted + 1, total, total))
                for c in shown:
                    print("\n%s\n%s" % (render_comment_header(c), c.body))
            else:
                print("[%s] %s" % (col, path))
                print(render_ticket(ticket))
        elif args.cmd == "comment":
            body = _body_from_args(args.body, args.body_file)
            n = add_comment(root, args.id, body, args.by, to=args.to, ask=args.ask,
                            refs=_parse_refs(args.re), commit=args.commit)
            print("message %d added to %s" % (n, validate_id(args.id)))
        elif args.cmd == "thread":
            body = _body_from_args(args.body, args.body_file)
            print(create_thread(root, args.title, body, args.by, to=args.to, ask=args.ask,
                                commit=args.commit, ticket=args.ticket))
        elif args.cmd == "threads":
            with board_lock(root):
                rows = list_threads(root)
            if args.json:
                print(json.dumps([ticket_to_dict(THREADS_DIR, t) | {"pending": len(pending_asks(t))}
                                  for t in rows], indent=2))
            else:
                _print_threads(rows)
        elif args.cmd == "inbox":
            rows = inbox_rows(root, args.name)
            if args.json:
                print(json.dumps(rows, indent=2))
            else:
                _print_inbox(rows)
```

The existing `except (KeyError, ValueError, RuntimeError, OSError)` at the end of the block already turns every refusal into `error: ...` on stderr and exit code 2.

- [ ] **Step 7: Run the new tests and the whole suite**

Run: `python3.14 -m unittest tests.test_board.TestPending tests.test_board.TestConversationCLI -v 2>&1 | tail -25`
Expected: 20 tests PASS.

Run: `python3.14 -m unittest discover -s tests -v 2>&1 | tail -5`
Expected: `OK`.

- [ ] **Step 8: Try it by hand, once**

```bash
cd "$(mktemp -d)" && python3.14 /Users/jimmy/Source/bronhills/agent-board/board.py init --no-agents
B="python3.14 /Users/jimmy/Source/bronhills/agent-board/board.py"
$B thread "Design A vs B" "Attack the filter parameter first" --by claude --to codex --ask --commit a1b2c3d
$B inbox codex
$B comment 1 "Changes requested: two blocking findings" --by codex --to claude --ask --re 1 --commit a1b2c3d
$B inbox codex
$B inbox claude
$B threads
$B show 1 --last 1
```

Expected: `inbox codex` shows message 1, then nothing pending; `inbox claude` shows message 2; `threads` shows `1 pending`; `show --last 1` says `1 earlier message omitted; showing 2-2 of 2`.

- [ ] **Step 9: Commit**

```bash
git add board.py tests/test_board.py
git commit -m "feat: inbox — pending asks computed from ask and re

board inbox <name> lists every ask addressed to name that no later
message in the same file answers with re. board thread, board threads,
show --last, and bodies from a file or stdin. Reads that compute pending
take the board lock so a mid-append header is never seen without its body."
```

---

### Task 5: Web UI — badges, threads, waiting strip, forms

The page grows a threads section, message badges, a waiting strip listing every pending ask, and forms that post the new fields. The reload guard is fixed to compare against `defaultValue`, because today any prefilled input, such as an owner in an assign form, silently stops the page refreshing.

**Files:**
- Modify: `board.py` — `PAGE_CSS`, `_render_card`, new `_render_comments`, `_comment_form`, `_render_thread_card`, `_render_waiting`, `render_board_html`, `_make_handler.do_POST`
- Modify: `tests/test_board.py` — `TestRefreshAndPort.test_reload_skips_when_a_field_has_text_but_no_focus` (one assertion), new class `TestConversationUI`

**Interfaces:**
- Consumes: `_all_items`, `pending_asks`, `_first_line`, `create_thread`, `add_comment`, `_parse_refs`, `board_lock`, `is_thread`, `THREADS_DIR`.
- Produces: HTML only. POST `/thread` with fields `title, body, by, to, ask, commit`. POST `/comment` accepts `to, ask, re, commit` in addition to `id, body, by`. Cards carry `id="card-<id>"`.

- [ ] **Step 1: Write the failing tests**

In `TestRefreshAndPort.test_reload_skips_when_a_field_has_text_but_no_focus`, change the assertion `self.assertIn("value.trim()", page)` to:

```python
        self.assertIn("defaultValue", page)
        self.assertNotIn("value.trim()", page)
```

Append to `tests/test_board.py`:

```python
class TestConversationUI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = board.init_board(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    @contextlib.contextmanager
    def server(self):
        srv = board.HTTPServer(("127.0.0.1", 0), board._make_handler(self.root))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            yield http.client.HTTPConnection("127.0.0.1", srv.server_port)
        finally:
            srv.shutdown()

    def post(self, conn, path, body):
        conn.request("POST", path, body, {"Content-Length": str(len(body)),
                                          "Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        resp.read()
        return resp.status

    def test_thread_card_renders_title_count_pending_and_link(self):
        board.create_ticket(self.root, "the work")
        tid = board.create_thread(self.root, "Design A vs B", "q", "claude", to="codex", ask=True, ticket="1")
        page = board.render_board_html(self.root)
        self.assertIn("Design A vs B", page)
        self.assertIn('id="card-%s"' % tid, page)
        self.assertIn("1 pending", page)
        self.assertIn("about 001", page)
        self.assertIn("Threads", page)

    def test_message_badges_and_numbers(self):
        tid = board.create_thread(self.root, "t", "q", "claude", to="codex", ask=True, commit="a1b2c3d")
        board.add_comment(self.root, tid, "a", "codex", to="claude", refs=[1])
        page = board.render_board_html(self.root)
        self.assertIn("to codex", page)
        self.assertIn(">ask<", page)
        self.assertIn("re 1", page)
        self.assertIn("a1b2c3d", page)
        self.assertIn("#1", page)
        self.assertIn("#2", page)

    def test_waiting_strip_lists_pending_and_empties_when_answered(self):
        tid = board.create_thread(self.root, "t", "please look", "claude", to="codex", ask=True)
        page = board.render_board_html(self.root)
        self.assertIn('class="wait"', page)
        self.assertIn('href="#card-%s"' % tid, page)
        self.assertIn("please look", page)
        board.add_comment(self.root, tid, "done", "codex", refs=[1])
        self.assertNotIn('class="wait"', board.render_board_html(self.root))

    def test_badges_are_escaped(self):
        tid = board.create_thread(self.root, "t", "q", "claude", to="<b>x</b>", ask=True)
        page = board.render_board_html(self.root)
        self.assertNotIn("<b>x</b>", page)

    def test_forms_carry_the_new_fields(self):
        board.create_ticket(self.root, "k")
        page = board.render_board_html(self.root)
        self.assertIn("action='/thread'", page)
        self.assertIn('name="to"', page)
        self.assertIn('name="ask"', page)
        self.assertIn('name="re"', page)

    def test_post_thread_matches_the_cli(self):
        with self.server() as conn:
            status = self.post(conn, "/thread", "title=Design&body=Opening+line&by=jimmy&to=codex&ask=1")
        self.assertEqual(status, 303)
        col, path = board.find_ticket(self.root, "1")
        self.assertEqual(col, board.THREADS_DIR)
        t = board.load_ticket(path)
        self.assertEqual((t.title, t.comments[0].by, t.comments[0].to, t.comments[0].ask, t.comments[0].body),
                         ("Design", "jimmy", "codex", True, "Opening line"))

    def test_post_comment_with_trailers(self):
        tid = board.create_thread(self.root, "t", "q", "claude", to="human", ask=True)
        with self.server() as conn:
            status = self.post(conn, "/comment", "id=%s&body=answer&to=claude&re=1&ask=1" % tid)
            self.assertEqual(status, 303)
            status = self.post(conn, "/comment", "id=%s&body=bad&re=zero" % tid)
            self.assertEqual(status, 400)
        _, path = board.find_ticket(self.root, tid)
        t = board.load_ticket(path)
        self.assertEqual(len(t.comments), 2)
        c = t.comments[1]
        self.assertEqual((c.by, c.to, c.re, c.ask), ("human", "claude", [1], True))
        self.assertEqual(board.pending_asks(t, "human"), [])

    def test_page_is_a_projection(self):
        tid = board.create_thread(self.root, "t", "q", "claude", to="codex", ask=True)
        before = board.render_board_html(self.root)
        board.add_comment(self.root, tid, "a", "codex", refs=[1])
        after = board.render_board_html(self.root)
        self.assertNotEqual(before, after)
        self.assertEqual(after, board.render_board_html(self.root))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3.14 -m unittest tests.test_board.TestConversationUI tests.test_board.TestRefreshAndPort -v 2>&1 | tail -20`
Expected: the new tests FAIL on missing markup and a 404 from `/thread`; the refresh test FAILS on `defaultValue`.

- [ ] **Step 3: CSS**

Append to `PAGE_CSS` (inside the triple-quoted string, before the closing `"""`):

```css
.num{font:600 11px ui-monospace,SFMono-Regular,Menlo,monospace;color:#8993a4;margin-right:6px}
.bg{display:inline-block;font-size:10.5px;font-weight:600;color:#42526e;background:#f4f5f7;
 border-radius:3px;padding:0 5px;margin-left:5px;vertical-align:1px}
.bg.ask{background:#fff0b3;color:#7f5f01}
.bg.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-weight:500}
.c.pend{border-left:3px solid #ff8b00;padding-left:7px;margin-left:-10px}
.pending{background:#ffebe6;color:#bf2600;border-radius:10px;padding:1px 8px;font-size:11px;font-weight:600}
.about{color:#6b778c;font-size:11px}
.wait{background:#fffae6;border:1px solid #ffe380;border-radius:4px;padding:10px 14px;margin:0 0 16px}
.wait h3{margin:0 0 6px;font-size:11px;font-weight:700;color:#7f5f01;text-transform:uppercase;letter-spacing:.08em}
.wait ul{margin:0;padding-left:18px;font-size:12.5px}
.wait li{margin:2px 0}
.wait a{color:#0052cc;text-decoration:none;font:600 11px ui-monospace,SFMono-Regular,Menlo,monospace}
.sec{display:flex;align-items:center;gap:7px;font-size:11px;font-weight:700;text-transform:uppercase;
 letter-spacing:.08em;color:#5e6c84;margin:22px 0 10px}
.threads{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:10px;align-items:start}
.threads .t{margin-bottom:0}
.sm{flex:0 0 74px}
label.ask{display:flex;align-items:center;gap:3px;font-size:11px;color:#42526e;white-space:nowrap}
label.ask input{flex:0 0 auto;min-width:0}
.adds{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-start}
.add .row{display:flex;gap:6px;margin-bottom:8px}
.add .row input{margin-bottom:0}
```

- [ ] **Step 4: Shared comment rendering and the two forms**

Add before `_render_card`:

```python
def _render_comments(t: Ticket) -> str:
    """Messages with their number and badges. Pending asks get a left rule."""
    esc = html.escape
    pending = {n for n, _ in pending_asks(t)}
    rows = []
    for n, c in enumerate(t.comments, start=1):
        badges = ""
        if c.to:
            badges += '<span class="bg">to %s</span>' % esc(c.to)
        if c.ask:
            badges += '<span class="bg ask">ask</span>'
        if c.re:
            badges += '<span class="bg">re %s</span>' % esc(",".join(str(x) for x in c.re))
        if c.commit:
            badges += '<span class="bg mono">%s</span>' % esc(c.commit)
        rows.append(
            '<div class="c%s"><span class="num">#%d</span><span class="who">%s</span>'
            '<span class="when">%s</span>%s<div class="body">%s</div></div>'
            % (" pend" if n in pending else "", n, esc(c.by),
               esc(c.at.replace("T", " ").rstrip("Z")), badges, esc(c.body))
        )
    return '<div class="comments">%s</div>' % "".join(rows) if rows else ""


def _comment_form(tid: str) -> str:
    esc = html.escape
    return (
        '<form class="inline" method="post" action="/comment">'
        '<input type="hidden" name="id" value="%s">'
        '<input name="body" placeholder="Add a message" required>'
        '<input class="sm" name="to" placeholder="to">'
        '<input class="sm" name="re" placeholder="re">'
        '<label class="ask"><input type="checkbox" name="ask" value="1">ask</label>'
        "<button>Send</button></form>" % esc(tid)
    )
```

In `_render_card`, delete the local `comment_form = (...)` block and the `comments = ...` block, and use the helpers. The function becomes:

```python
def _render_card(t, col: str) -> str:
    """One ticket card: identity, body, messages, then actions behind a toggle."""
    esc = html.escape
    moves = "".join(
        '<form class="inline" method="post" action="/move">'
        '<input type="hidden" name="id" value="%s">'
        '<input type="hidden" name="column" value="%s">'
        "<button>%s</button></form>" % (esc(t.id), esc(c), esc(c))
        for c in COLUMNS if c != col
    )
    assign_form = (
        '<form class="inline" method="post" action="/assign">'
        '<input type="hidden" name="id" value="%s">'
        '<input name="owner" placeholder="Assign to (hint)" value="%s">'
        "<button>Set</button></form>" % (esc(t.id), esc(t.owner or ""))
    )
    desc = '<div class="desc">%s</div>' % esc(t.description) if t.description else ""
    owner = '<span class="own">%s</span>' % esc(t.owner) if t.owner else ""
    count = ('<span class="cnt">%d message%s</span>'
             % (len(t.comments), "" if len(t.comments) == 1 else "s")) if t.comments else ""
    pend = len(pending_asks(t))
    pending = '<span class="pending">%d pending</span>' % pend if pend else ""
    return (
        '<div class="t" id="card-%s">'
        '<div class="title">%s</div>%s'
        '<div class="meta"><span class="id">%s</span>%s%s%s</div>'
        "%s"
        '<details><summary>Actions</summary><div class="acts">'
        '<div class="pills">%s</div>%s%s</div></details>'
        "</div>"
        % (esc(t.id), esc(t.title), desc, esc(t.id), owner, count, pending,
           _render_comments(t), moves, assign_form, _comment_form(t.id))
    )


def _render_thread_card(t: Ticket) -> str:
    """A thread has no column and no owner: title, messages, reply form."""
    esc = html.escape
    count = '<span class="cnt">%d message%s</span>' % (len(t.comments), "" if len(t.comments) == 1 else "s")
    pend = len(pending_asks(t))
    pending = '<span class="pending">%d pending</span>' % pend if pend else ""
    about = '<span class="about">about %s</span>' % esc(t.ticket) if t.ticket else ""
    return (
        '<div class="t" id="card-%s">'
        '<div class="title">%s</div>'
        '<div class="meta"><span class="id">%s</span>%s%s%s</div>'
        "%s"
        '<details><summary>Reply</summary><div class="acts">%s</div></details>'
        "</div>"
        % (esc(t.id), esc(t.title), esc(t.id), count, pending, about,
           _render_comments(t), _comment_form(t.id))
    )


def _render_waiting(items: list[tuple[str, Ticket]]) -> str:
    """Every pending ask on the board, newest first. `board inbox` with no
    name, rendered. No input and no filter, so nothing fights the refresh."""
    esc = html.escape
    rows = []
    for col, t in items:
        for n, c in pending_asks(t):
            rows.append((c.at, t, n, c))
    if not rows:
        return ""
    rows.sort(key=lambda r: (r[0], int(r[1].id), r[2]), reverse=True)
    lis = "".join(
        '<li><a href="#card-%s">%s #%d</a> <b>%s</b> to <b>%s</b>: %s</li>'
        % (esc(t.id), esc(t.id), n, esc(c.by), esc(c.to), esc(_first_line(c.body)))
        for _, t, n, c in rows
    )
    return '<div class="wait"><h3>Waiting</h3><ul>%s</ul></div>' % lis
```

- [ ] **Step 5: The page**

Replace `render_board_html`:

```python
def render_board_html(root: str) -> str:
    esc = html.escape
    with board_lock(root):
        items = _all_items(root)
    by_col: dict[str, list[Ticket]] = {col: [] for col in COLUMNS}
    threads: list[Ticket] = []
    for col, t in items:
        (threads if is_thread(col) else by_col[col]).append(t)
    total = sum(len(v) for v in by_col.values())
    cols_html = []
    for col in COLUMNS:
        rows = by_col[col]
        cards = "".join(_render_card(t, col) for t in rows)
        cols_html.append(
            '<div class="col"><h2><span class="dot" style="background:%s"></span>%s'
            '<span class="n">%d</span></h2>%s</div>'
            % (COLUMN_ACCENT.get(col, "#5e6c84"), esc(col), len(rows),
               cards or '<div class="empty">Nothing here</div>')
        )
    thread_cards = "".join(_render_thread_card(t) for t in threads) \
        or '<div class="empty">No threads yet</div>'
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        # A meta refresh reloads mid-typing and destroys whatever is in a form.
        # Poll instead, and skip the reload while the user is editing: a field
        # counts as edited when its value differs from what the page loaded,
        # so a prefilled owner or author does not freeze the page forever.
        "<script>setInterval(function(){"
        "var a=document.activeElement;"
        "if(a&&(a.tagName==='INPUT'||a.tagName==='TEXTAREA'))return;"
        "var f=document.querySelectorAll('input[type=text],input:not([type]),textarea');"
        "for(var i=0;i<f.length;i++){if(f[i].value!==f[i].defaultValue)return;}"
        "location.reload();},3000);</script>"
        "<title>agent-board</title><style>%s</style></head><body>"
        "<h1>agent-board</h1>"
        "<p class='sub'>%d ticket%s &middot; %d thread%s &middot; %s</p>"
        "%s"
        "<div class='cols'>%s</div>"
        "<h2 class='sec'>Threads <span class='n'>%d</span></h2>"
        "<div class='threads'>%s</div>"
        "<div class='adds'>"
        "<div class='add'><h3>New ticket</h3><form method='post' action='/new'>"
        "<input name='title' placeholder='Title' required>"
        "<textarea name='desc' rows='3' placeholder='Description (optional)'></textarea>"
        "<button>Create</button></form></div>"
        "<div class='add'><h3>New thread</h3><form method='post' action='/thread'>"
        "<input name='title' placeholder='Title' required>"
        "<textarea name='body' rows='3' placeholder='Opening message' required></textarea>"
        "<div class='row'><input name='by' placeholder='by' value='human'>"
        "<input name='to' placeholder='to'>"
        "<label class='ask'><input type='checkbox' name='ask' value='1'>ask</label></div>"
        "<button>Start thread</button></form></div>"
        "</div>"
        "</body></html>"
        % (PAGE_CSS, total, "" if total == 1 else "s", len(threads), "" if len(threads) == 1 else "s",
           esc(_project_label(root)), _render_waiting(items), "".join(cols_html),
           len(threads), thread_cards)
    )
```

- [ ] **Step 6: POST handlers**

In `_make_handler.do_POST`, replace the `/comment` branch and add `/thread`:

```python
                elif path == "/comment":
                    add_comment(root, (fields.get("id") or [""])[0],
                                (fields.get("body") or [""])[0],
                                (fields.get("by") or ["human"])[0],
                                to=(fields.get("to") or [""])[0].strip() or None,
                                ask="ask" in fields,
                                refs=_parse_refs((fields.get("re") or [""])[0]),
                                commit=(fields.get("commit") or [""])[0].strip() or None)
                elif path == "/thread":
                    title = (fields.get("title") or [""])[0].strip()
                    if title:
                        create_thread(root, title, (fields.get("body") or [""])[0],
                                      (fields.get("by") or ["human"])[0].strip() or "human",
                                      to=(fields.get("to") or [""])[0].strip() or None,
                                      ask="ask" in fields,
                                      commit=(fields.get("commit") or [""])[0].strip() or None)
```

- [ ] **Step 7: Run the tests**

Run: `python3.14 -m unittest tests.test_board.TestConversationUI tests.test_board.TestRefreshAndPort tests.test_board.TestUIComments tests.test_board.TestWebUI -v 2>&1 | tail -30`
Expected: all PASS.

Run: `python3.14 -m unittest discover -s tests -v 2>&1 | tail -5`
Expected: `OK`.

- [ ] **Step 8: Check it in a browser. Not optional.**

Two layout bugs shipped past a green suite because no test renders a page. Seed a board and look at it.

```bash
cd "$(mktemp -d)" && B="python3.14 /Users/jimmy/Source/bronhills/agent-board/board.py"
$B init --no-agents
$B new "Fix login redirect loop" --desc "401 does not clear the cookie"
$B new "Add rate limiting" --column review --owner codex
$B comment 2 "Please review at a1b2c3d" --by claude --to codex --ask --commit a1b2c3d
$B thread "Shelve/restore design, before any spec" "Attack the optional filter parameter first. It is the part I trust least." --by claude --to codex --ask --commit a1b2c3d --ticket 1
$B comment 3 "Changes requested. Two blocking findings: the identity is not read-only, and the optional filter preserves the omission hazard." --by codex --to claude --ask --re 1 --commit a1b2c3d
$B thread "Quiet thread with no asks" "Just a note for the record." --by human
$B serve --port 8899
```

Open `http://127.0.0.1:8899` with the `/browse` skill (the user's standing instruction is to use gstack `/browse` for all web browsing) and check every line:

- The waiting strip is at the top and lists two asks, newest first, each link jumping to its card.
- Five columns still render side by side at 1280 px wide with no horizontal scrollbar on the page.
- The Threads section sits below the columns with two cards in a grid, the design thread showing `1 pending` and `about 001`, the quiet thread showing neither.
- Messages show `#1`, `#2`, the author, badges `to codex`, `ask`, `re 1`, `a1b2c3d`, and the pending message has an orange left rule.
- Reply from the browser on the design thread: body `Approved`, to `codex`, re `2`, no ask. After the 303 the strip loses that ask and the card loses its pending badge.
- Start a thread from the New thread form with `to` set and `ask` ticked. It appears with `1 pending`.
- Leave the page alone for 10 seconds with a prefilled owner on a card. It refreshes. Type in any field and stop; it does not refresh while your text differs from the loaded value.
- Narrow the window to 800 px. Columns wrap or scroll inside `.cols`, the threads grid drops to one column, and the page body still has no horizontal scrollbar.

Fix anything wrong, re-run the suite, and only then continue.

- [ ] **Step 9: Commit**

```bash
git add board.py tests/test_board.py
git commit -m "feat: threads, badges and a waiting strip in the web UI

Threads render below the columns; every message shows its number and its
to, ask, re and commit badges; a strip at the top lists every pending ask
and links to it. Forms post the new fields through the same functions the
CLI calls. The reload guard now compares against defaultValue, so a
prefilled owner no longer freezes the page."
```

---

### Task 6: Agents block, README, decisions

Teach agents the new commands, tell humans what changed, and record the rulings.

**Files:**
- Modify: `board.py` — `agents_block`
- Modify: `README.md`, `docs/decisions.md`, `AGENTS.md` (this repo's own instructions)
- Modify: `docs/board-ui.png` — retake
- Test: `tests/test_board.py` — one test added to `TestAgentsDoc`

**Interfaces:**
- Consumes: nothing new.
- Produces: documentation. The agents block must keep the strings `board take` and `ask` that `TestAgentsDoc` already asserts.

- [ ] **Step 1: Write the failing test**

Add to `TestAgentsDoc`:

```python
    def test_block_teaches_inbox_threads_and_re(self):
        board.write_agents_doc(self.base)
        text = self.read("AGENTS.md")
        for needle in ("board inbox", "board thread", "--re", "--ask", "--body-file", "nudge"):
            self.assertIn(needle, text, needle)
```

Run: `python3.14 -m unittest tests.test_board.TestAgentsDoc -v 2>&1 | tail -5`
Expected: the new test FAILS on `board inbox`.

- [ ] **Step 2: Rewrite `agents_block`**

Replace the function body's string:

```python
    return """%s
## Task board

This project uses agent-board. The board lives in `.agent-board/`: each column
is a directory, each ticket is a markdown file, and `threads/` holds
conversations that are not about a ticket.

    board inbox <you>                 what is waiting on you. Run this first.
    board list                        see the whole board
    board show 7                      read a ticket or thread, with its messages
    board show 7 --last 3             only the latest messages
    board take 7 --owner <you>        claim a ticket (moves to doing/)
    board comment 7 "..." --by <you>  report back on it
    board move 7 review               hand it on
    board thread "title" "..." --by <you> --to <them> --ask
                                      start a conversation with another agent
    board threads                     list conversations

Use your own name or role as `<you>`, whatever the user calls you, and use it
consistently. If the user has not told you which column is yours, ask, or take
the top of `todo`. Read the ticket before starting, and report with
`board comment` rather than only in chat, so the next agent sees what you did.

Talking to another agent: add `--to <them>` and `--ask` to `board comment` or
`board thread` when you need an answer. Answer with `--re <n>`, listing every
message number you are answering, and add `--ask` again if your answer needs
one. Nothing is answered until a later message says `--re`, so trust
`board inbox <you>`, not your memory. Put a one-line summary on the first line
of every message; for anything longer than a paragraph use `--body-file PATH`,
or `-` to read stdin.

After you post, nudge the recipient yourself if you know its pane, for example
with `cmux send`. The board never notifies anyone, and the nudge must carry no
content: the message is in the file.
%s""" % (AGENTS_BEGIN, AGENTS_END)
```

Run: `python3.14 -m unittest tests.test_board.TestAgentsDoc -v 2>&1 | tail -5`
Expected: all PASS, including `test_block_is_role_neutral`.

- [ ] **Step 3: README**

In `README.md`:

1. In the `## Use` code block, after the `board assign 1 codex` line and before `board serve`, add:

```
board inbox codex                       # what is waiting on codex
board thread "Design A vs B" "Attack the filter first" --by claude --to codex --ask
board comment 2 "Changes requested" --by codex --to claude --ask --re 1 --commit a1b2c3d
board threads                           # conversations, newest activity first
board show 2 --last 3                   # just the latest messages
```

2. After the `### Standing roles` section and before `### Auto-nudge (optional)`, add a section titled `### Talking to each other` with this content:

```markdown
Not every conversation is about a ticket. When this board was built, Claude and
Codex argued design, plan, and every task across many rounds, and none of it
survived, because there was no ticket to hang it on. So the board has threads:

    board thread "Shelve/restore design" "Attack the filter parameter first" --by claude --to codex --ask
    board inbox codex            # 003 thread #1 claude to codex ...
    board comment 3 "Two blocking findings ..." --by codex --to claude --ask --re 1 --commit a1b2c3d
    board inbox codex            # (nothing pending)
    board inbox claude           # 003 thread #2 codex to claude ...

A thread is a markdown file under `.agent-board/threads/`, same shape as a
ticket, no column. Every message on a ticket or a thread can carry four
trailers: `to`, `ask`, `re`, and `commit`. **An ask is pending until a later
message in the same file lists it in `re`.** That is the whole inbox: no state,
no counters, no message types. Changes requested is a reply that asks again;
approval is a reply alone; blocked is an ask to the human; superseding three
open requests at once is `--re 5,6,7 --ask`. `board inbox` with no name shows
everything pending, which is also the Waiting strip at the top of the web UI.

Bodies are markdown. Use `--body-file PATH`, or `-` for stdin, for anything
longer than a paragraph.
```

3. Under `## Rules`, add a bullet:

```markdown
- **Nothing is answered until something says `re`.** Do not infer from who spoke last; the bridge's tool did, and hid real requests twice.
```

4. Under `## Known limitations`, add:

```markdown
- **Message numbers are positions.** `re 4` means the fourth message in that file. Appending never renumbers, but a hand edit that inserts or deletes a message does, and every later `re` then points at the wrong thing. Edit bodies if you must; never insert or remove a header.
```

5. Under `## Design`, add a line for the new spec:

```markdown
- [`docs/superpowers/specs/2026-09-05-threads-and-inbox-design.md`](docs/superpowers/specs/2026-09-05-threads-and-inbox-design.md) — threads and the inbox
```

- [ ] **Step 4: Decisions**

Append to `docs/decisions.md`:

```markdown
## 2026-09-05 — threads and inbox

The bridge's wake-up machinery is gone for good: with cmux the poster nudges the
recipient. What the board lacked was the conversation itself. Measured across the
41 threads and 549 messages in produxiom2 before designing, and reviewed by Codex
against the code before building.

| Decision | Why |
|---|---|
| Threads are column-less, with no status and no close | Liveness is "has an unanswered ask". A status field is what the board refused for tickets, for the same reason. |
| No message types; two trailers, `ask` and `re` | Twelve types plus thirteen ad hoc ones reduce to two facts. `re` is a list because one thread superseded three open requests at once. |
| Pending is computed from `ask` and `re`, never from who spoke last | produxiom2's tool guessed from the newest message's type and was patched twice after hiding a real request. |
| Names are free strings; no roster, no aliases | The claude/engineer split came from renaming mid-flight. The fix is choosing a name once. |
| The board never notifies and never runs git | The poster nudges. The human commits. The files are unignored, so committing the board commits the conversation. |
| One file per thread, appended under the lock | Codex first argued for one file per message and withdrew it for a one-machine board. What it asked for instead, and got: readers that compute pending take the lock, and an append is one write loop. |
| The header parser anchors on the timestamp | Anchoring on the first separator read an author of `alice · <ts> · to bob` and a timestamp of `ask`. Codex found it. |
| Header-shaped body lines get a leading backslash on write | With `re`, a pasted example could clear a real request. A space would not survive the body `strip()`. |
| The allocator scan runs under the board lock | It did not, and two creators could both win the same id after the first's reservation was renamed away. Codex reproduced it. |
| Message numbers are positions, not ids | Adequate while appends never renumber. Stable ids are the fallback if hand edits to committed threads become common. |
| `watch threads` and a per-name UI filter were cut | The first re-tests the mtime mechanism; the second fights the refresh guard and the POST redirect. `board inbox <name>` is the per-name view. |
| The `ticket` link on a thread stays, optional | Codex would cut it. The human named conversations about tickets as a real case, and it is one field shown as text. |
```

- [ ] **Step 5: This repo's own AGENTS.md**

In `AGENTS.md` at the repo root, after the `## Design` list, add the new spec and plan:

```markdown
`docs/superpowers/specs/2026-09-05-threads-and-inbox-design.md` — threads and inbox, what and why.
`docs/superpowers/plans/2026-09-05-threads-and-inbox.md` — how they were built.
```

And in the `## Before proposing any feature` paragraph, extend the list of rejected things: after `presence indicators`, add `, message types, thread status, a per-name inbox filter`.

- [ ] **Step 6: Retake the screenshot**

With the seeded board from Task 5 Step 8 still served, capture the page at about 1280 px wide showing the waiting strip, the columns, and the threads section, and save it over `docs/board-ui.png`. Use the `/browse` skill's screenshot. Check the README renders it.

- [ ] **Step 7: Run everything one last time**

Run: `python3.14 -m unittest discover -s tests -v 2>&1 | tail -5`
Expected: `OK`.

Run: `git status --short`
Expected: only the files this task touched.

- [ ] **Step 8: Commit**

```bash
git add board.py tests/test_board.py README.md docs/decisions.md AGENTS.md docs/board-ui.png
git commit -m "docs: teach agents and humans about threads and the inbox

The agents block leads with board inbox, explains ask and re, and says
the poster nudges. README gains a section on talking to each other, and
decisions.md records the twelve rulings from the design and the review."
```

---

## Self-review against the spec

**Spec coverage.**
- Storage, `threads/`, `ticket` field: Task 3.
- Header grammar, anchored parse, read and write rules, sanitising, spoof neutralising: Task 2.
- Batch supersession, `re` as a list: Task 2 grammar, Task 4 pending test.
- Ids shared, allocator race fixed first, `find_ticket` ambiguity, refusal inside mutators, boards without `threads/`: Tasks 1 and 3.
- Readers and writers, single write loop, reader lock in `inbox`, `show`, `threads`, page render: Tasks 2, 4, 5.
- Inbox rule: Task 4.
- CLI, every command and flag in the spec's block, `--body-file`, `show --last` with original numbers: Task 4.
- Web UI panel, badges, waiting strip, forms, `/thread`: Task 5. Browser check: Task 5 Step 8.
- Agents block: Task 6.
- Build order in the spec: matches Tasks 1 to 6.
- Testing section: every bullet maps to a test in Tasks 1 to 6; the browser bullet is Task 5 Step 8.
- Decisions to record: Task 6 Step 4.

**Deviation from the spec, recorded.** Neutralising uses a leading backslash, not a leading space; Task 2 Step 8 corrects the spec sentence. Reason in Task 2 Step 5.

**Type consistency.** `add_comment(..., refs=...)` everywhere, never `re=` as a parameter; the `Comment` field is `re`. `_create_locked` returns `(tid, path)` in Tasks 1 and 3. `pending_asks` returns `list[tuple[int, Comment]]` and is consumed as such in Tasks 4 and 5. `inbox_rows` row keys match between Task 4's function and its test. `THREADS_DIR` is the column value `find_ticket` returns for a thread and `ticket_to_dict` maps to `kind == "thread"`.
