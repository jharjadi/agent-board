# agent-board Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A file-backed kanban board that lets several coding agents and a human coordinate work, where directories are columns and tickets are markdown files.

**Architecture:** One dependency-free Python file. Directories under `.agent-board/` are columns; a ticket is one markdown file with frontmatter and appended comment sections. The directory is the only source of truth — moving a column is `mv`. A CLI serves agents; a stdlib HTTP server serves the human as a pure projection over the filesystem.

**Tech Stack:** Python 3.9+ standard library only. `unittest` for tests, `http.server` for the UI. No third-party packages anywhere in the repo.

**Spec:** `docs/superpowers/specs/2026-09-04-agent-board-design.md`

## Global Constraints

- **Python 3.9+ compatible.** No `match`, no `X | Y` unions, no builtin generic subscripts at runtime. Use `typing.Optional` / `typing.List` / `typing.Dict`.
- **Standard library only.** No third-party imports in `board.py` or in tests.
- **Single file.** All implementation lives in `board.py` at the repo root. This is a packaging requirement — the file must be copyable, symlinkable, or `curl`-able on its own.
- **Columns are fixed:** `todo`, `doing`, `review`, `blocked`, `done`.
- **The directory is the only truth.** `status` must never appear in frontmatter.
- **All writes atomic:** temp file in the destination directory, then `os.replace`.
- **Validate before touching paths.** Ids match `^[0-9]{1,6}$`; columns must be in the fixed set. Ticket ids become file paths.
- **Timestamps** are ISO-8601 UTC with a `Z` suffix, e.g. `2026-09-04T13:53:00Z`.
- Run tests with `python3 -m unittest discover -s tests -v`.

---

## File Structure

| File | Responsibility |
|---|---|
| `board.py` | Everything: parsing, board operations, CLI dispatch, web UI |
| `tests/test_board.py` | Full test suite |
| `board` | Executable shim → runs `board.py` with the caller's arguments |
| `README.md` | Install, usage, agent wiring |

---

### Task 1: Ticket parsing and rendering

**Files:**
- Create: `board.py`
- Create: `tests/test_board.py`

**Interfaces:**
- Consumes: nothing
- Produces: `Comment` dataclass (`by: str`, `at: str`, `body: str`); `Ticket` dataclass (`id: str`, `title: str`, `created: str`, `description: str`, `owner: Optional[str]`, `branch: Optional[str]`, `comments: List[Comment]`); `parse_ticket(text: str) -> Ticket`; `render_ticket(t: Ticket) -> str`; `parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]`; `COLUMNS: Tuple[str, ...]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_board.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'board'`

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
"""agent-board - a file-backed kanban board for coordinating coding agents."""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

COLUMNS = ("todo", "doing", "review", "blocked", "done")
FM_DELIM = "---"
COMMENT_RE = re.compile(r"^## comment — (?P<by>.+?) · (?P<at>\S+)\s*$")


@dataclass
class Comment:
    by: str
    at: str
    body: str


@dataclass
class Ticket:
    id: str
    title: str
    created: str
    description: str = ""
    owner: Optional[str] = None
    branch: Optional[str] = None
    comments: List[Comment] = field(default_factory=list)


def parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    lines = text.split("\n")
    if not lines or lines[0].strip() != FM_DELIM:
        raise ValueError("missing frontmatter")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == FM_DELIM:
            end = i
            break
    if end is None:
        raise ValueError("unterminated frontmatter")
    meta = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1]
        meta[key.strip()] = value
    return meta, "\n".join(lines[end + 1:]).lstrip("\n")


def parse_ticket(text: str) -> Ticket:
    meta, body = parse_frontmatter(text)
    for required in ("id", "title", "created"):
        if required not in meta:
            raise ValueError("ticket missing required field: %s" % required)
    desc: List[str] = []
    comments: List[Comment] = []
    current = None
    buf: List[str] = []
    for line in body.split("\n"):
        match = COMMENT_RE.match(line)
        if match:
            if current is None:
                desc = buf
            else:
                comments.append(Comment(current[0], current[1], "\n".join(buf).strip()))
            current = (match.group("by"), match.group("at"))
            buf = []
        else:
            buf.append(line)
    if current is None:
        desc = buf
    else:
        comments.append(Comment(current[0], current[1], "\n".join(buf).strip()))
    return Ticket(
        id=meta["id"],
        title=meta["title"],
        created=meta["created"],
        description="\n".join(desc).strip(),
        owner=meta.get("owner") or None,
        branch=meta.get("branch") or None,
        comments=comments,
    )


def render_ticket(t: Ticket) -> str:
    out = [FM_DELIM, 'id: "%s"' % t.id, "title: %s" % t.title]
    if t.owner:
        out.append("owner: %s" % t.owner)
    out.append("created: %s" % t.created)
    if t.branch:
        out.append("branch: %s" % t.branch)
    out.append(FM_DELIM)
    text = "\n".join(out) + "\n\n" + t.description.strip() + "\n"
    for c in t.comments:
        text += "\n## comment — %s · %s\n%s\n" % (c.by, c.at, c.body.strip())
    return text
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS — 5 tests

- [ ] **Step 5: Commit**

```bash
git add board.py tests/test_board.py
git commit -m "feat: ticket parsing and rendering"
```

---

### Task 2: Board layout, validation, and `init`

**Files:**
- Modify: `board.py`
- Modify: `tests/test_board.py`

**Interfaces:**
- Consumes: `COLUMNS`
- Produces: `BOARD_DIR = ".agent-board"`; `validate_id(value: str) -> str` (returns zero-padded to 3); `validate_column(value: str) -> str`; `find_root(start: Optional[str] = None) -> str`; `init_board(base: Optional[str] = None) -> str`; `utc_now() -> str`; `slugify(title: str) -> str`; `atomic_write(path: str, text: str) -> None`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_board.py
import tempfile


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -v`
Expected: FAIL — `AttributeError: module 'board' has no attribute 'init_board'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to board.py (imports go at the top of the file)
import os
import sys
import tempfile
from datetime import datetime, timezone

BOARD_DIR = ".agent-board"
ID_RE = re.compile(r"^[0-9]{1,6}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_id(value: str) -> str:
    if not isinstance(value, str) or not ID_RE.match(value):
        raise ValueError("invalid ticket id: %r" % (value,))
    return value.zfill(3)


def validate_column(value: str) -> str:
    if value not in COLUMNS:
        raise ValueError("unknown column: %r (expected one of %s)" % (value, ", ".join(COLUMNS)))
    return value


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60] or "ticket"


def init_board(base: Optional[str] = None) -> str:
    base = base or os.getcwd()
    root = os.path.join(base, BOARD_DIR)
    for col in COLUMNS:
        os.makedirs(os.path.join(root, col), exist_ok=True)
    return root


def find_root(start: Optional[str] = None) -> str:
    path = os.path.abspath(start or os.getcwd())
    while True:
        candidate = os.path.join(path, BOARD_DIR)
        if os.path.isdir(candidate):
            return candidate
        parent = os.path.dirname(path)
        if parent == path:
            sys.exit("no %s found in %s or any parent. Run: board init"
                     % (BOARD_DIR, start or os.getcwd()))
        path = parent


def atomic_write(path: str, text: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS — 13 tests

- [ ] **Step 5: Commit**

```bash
git add board.py tests/test_board.py
git commit -m "feat: board layout, path validation, init"
```

---

### Task 3: Creating tickets with collision-safe ids

**Files:**
- Modify: `board.py`
- Modify: `tests/test_board.py`

**Interfaces:**
- Consumes: `Ticket`, `render_ticket`, `validate_column`, `slugify`, `utc_now`, `COLUMNS`
- Produces: `_all_ticket_paths(root: str) -> List[str]`; `next_id(root: str) -> str`; `create_ticket(root: str, title: str, description: str = "", column: str = "todo", owner: Optional[str] = None) -> str` returning the new zero-padded id

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_board.py
import threading


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

    def test_concurrent_create_never_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = board.init_board(tmp)
            results = []
            lock = threading.Lock()

            def make(n):
                tid = board.create_ticket(root, "ticket %d" % n)
                with lock:
                    results.append(tid)

            threads = [threading.Thread(target=make, args=(i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(len(results), 10)
            self.assertEqual(len(set(results)), 10, "duplicate ids: %s" % results)

    def test_rejects_bad_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = board.init_board(tmp)
            with self.assertRaises(ValueError):
                board.create_ticket(root, "x", column="../escape")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -v`
Expected: FAIL — `AttributeError: module 'board' has no attribute 'create_ticket'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to board.py
import glob

MAX_ID_ATTEMPTS = 5


def _all_ticket_paths(root: str) -> List[str]:
    paths = []
    for col in COLUMNS:
        paths.extend(glob.glob(os.path.join(root, col, "*.md")))
    return paths


def next_id(root: str) -> str:
    highest = 0
    for path in _all_ticket_paths(root):
        head = os.path.basename(path).split("-", 1)[0]
        if head.isdigit():
            highest = max(highest, int(head))
    return str(highest + 1).zfill(3)


def create_ticket(root: str, title: str, description: str = "",
                  column: str = "todo", owner: Optional[str] = None) -> str:
    column = validate_column(column)
    for _ in range(MAX_ID_ATTEMPTS):
        tid = next_id(root)
        path = os.path.join(root, column, "%s-%s.md" % (tid, slugify(title)))
        ticket = Ticket(id=tid, title=title, created=utc_now(),
                        description=description, owner=owner)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(render_ticket(ticket))
        return tid
    raise RuntimeError("could not allocate a ticket id after %d attempts" % MAX_ID_ATTEMPTS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS — 18 tests

- [ ] **Step 5: Commit**

```bash
git add board.py tests/test_board.py
git commit -m "feat: create tickets with collision-safe ids"
```

---

### Task 4: Reading — lookup, listing, JSON projection

**Files:**
- Modify: `board.py`
- Modify: `tests/test_board.py`

**Interfaces:**
- Consumes: `parse_ticket`, `validate_id`, `validate_column`, `COLUMNS`
- Produces: `load_ticket(path: str) -> Ticket`; `find_ticket(root: str, tid: str) -> Tuple[str, str]` returning `(column, path)` and raising `KeyError` when absent; `list_tickets(root: str, column: Optional[str] = None) -> List[Tuple[str, Ticket]]` sorted by id; `ticket_to_dict(column: str, t: Ticket) -> Dict`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_board.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -v`
Expected: FAIL — `AttributeError: module 'board' has no attribute 'find_ticket'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to board.py
from dataclasses import asdict


def load_ticket(path: str) -> Ticket:
    with open(path, encoding="utf-8") as fh:
        return parse_ticket(fh.read())


def find_ticket(root: str, tid: str) -> Tuple[str, str]:
    tid = validate_id(tid)
    for col in COLUMNS:
        matches = sorted(glob.glob(os.path.join(root, col, "%s-*.md" % tid)))
        if matches:
            return col, matches[0]
    raise KeyError("no ticket with id %s" % tid)


def list_tickets(root: str, column: Optional[str] = None) -> List[Tuple[str, Ticket]]:
    cols = (validate_column(column),) if column else COLUMNS
    rows = []
    for col in cols:
        for path in sorted(glob.glob(os.path.join(root, col, "*.md"))):
            rows.append((col, load_ticket(path)))
    rows.sort(key=lambda row: row[1].id)
    return rows


def ticket_to_dict(column: str, t: Ticket) -> Dict:
    data = asdict(t)
    data["column"] = column
    return data
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS — 24 tests

- [ ] **Step 5: Commit**

```bash
git add board.py tests/test_board.py
git commit -m "feat: ticket lookup, listing, and JSON projection"
```

---

### Task 5: Mutations — move, take, comment

**Files:**
- Modify: `board.py`
- Modify: `tests/test_board.py`

**Interfaces:**
- Consumes: `find_ticket`, `load_ticket`, `render_ticket`, `atomic_write`, `validate_column`, `utc_now`, `Comment`
- Produces: `move_ticket(root: str, tid: str, column: str) -> str` returning the new path; `take_ticket(root: str, tid: str, owner: str) -> str`; `add_comment(root: str, tid: str, body: str, by: str) -> None`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_board.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -v`
Expected: FAIL — `AttributeError: module 'board' has no attribute 'move_ticket'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to board.py
def move_ticket(root: str, tid: str, column: str) -> str:
    column = validate_column(column)
    _, path = find_ticket(root, tid)
    dest = os.path.join(root, column, os.path.basename(path))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    os.replace(path, dest)
    return dest


def take_ticket(root: str, tid: str, owner: str) -> str:
    _, path = find_ticket(root, tid)
    ticket = load_ticket(path)
    ticket.owner = owner
    atomic_write(path, render_ticket(ticket))
    return move_ticket(root, tid, "doing")


def add_comment(root: str, tid: str, body: str, by: str) -> None:
    _, path = find_ticket(root, tid)
    ticket = load_ticket(path)
    ticket.comments.append(Comment(by=by, at=utc_now(), body=body))
    atomic_write(path, render_ticket(ticket))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS — 29 tests

- [ ] **Step 5: Commit**

```bash
git add board.py tests/test_board.py
git commit -m "feat: move, take, and comment on tickets"
```

---

### Task 6: Column watcher

**Files:**
- Modify: `board.py`
- Modify: `tests/test_board.py`

**Interfaces:**
- Consumes: `validate_column`
- Produces: `column_snapshot(root: str, column: str) -> Dict[str, float]` mapping ticket id to mtime; `watch_once(root: str, column: str, seen: Dict[str, float]) -> Tuple[List[str], Dict[str, float]]` returning changed ids and the new snapshot

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_board.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -v`
Expected: FAIL — `AttributeError: module 'board' has no attribute 'watch_once'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to board.py
def column_snapshot(root: str, column: str) -> Dict[str, float]:
    column = validate_column(column)
    snap = {}
    for path in glob.glob(os.path.join(root, column, "*.md")):
        head = os.path.basename(path).split("-", 1)[0]
        if head.isdigit():
            snap[head] = os.path.getmtime(path)
    return snap


def watch_once(root: str, column: str, seen: Dict[str, float]) -> Tuple[List[str], Dict[str, float]]:
    current = column_snapshot(root, column)
    changed = sorted(tid for tid, mtime in current.items() if seen.get(tid) != mtime)
    return changed, current
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS — 33 tests

- [ ] **Step 5: Commit**

```bash
git add board.py tests/test_board.py
git commit -m "feat: column watcher emitting changed ticket ids"
```

---

### Task 7: CLI dispatch

**Files:**
- Modify: `board.py`
- Modify: `tests/test_board.py`

**Interfaces:**
- Consumes: every function from Tasks 2-6
- Produces: `main(argv: Optional[List[str]] = None) -> int`

Note: `main` dispatches `serve`, which Task 8 defines. To keep this task independently
runnable, add this stub immediately above `main` now; Task 8 replaces it.

```python
def serve(root: str, port: int = 8899) -> None:
    raise NotImplementedError("implemented in Task 8")
```

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_board.py
import contextlib
import io
import json


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -v`
Expected: FAIL — `AttributeError: module 'board' has no attribute 'main'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to board.py
import argparse
import json
import time


def _print_rows(rows) -> None:
    if not rows:
        print("(empty)")
        return
    for col, t in rows:
        owner = " @%s" % t.owner if t.owner else ""
        print("%s  %-8s %s%s" % (t.id, col, t.title, owner))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="board",
                                     description="file-backed kanban for coding agents")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("init")

    p = sub.add_parser("new")
    p.add_argument("title")
    p.add_argument("--desc", default="")
    p.add_argument("--column", default="todo")
    p.add_argument("--owner", default=None)

    p = sub.add_parser("list")
    p.add_argument("column", nargs="?", default=None)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("show")
    p.add_argument("id")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("take")
    p.add_argument("id")
    p.add_argument("--owner", required=True)

    p = sub.add_parser("move")
    p.add_argument("id")
    p.add_argument("column")

    p = sub.add_parser("comment")
    p.add_argument("id")
    p.add_argument("body")
    p.add_argument("--by", required=True)

    p = sub.add_parser("watch")
    p.add_argument("column")
    p.add_argument("--interval", type=float, default=5.0)

    p = sub.add_parser("serve")
    p.add_argument("--port", type=int, default=8899)

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 1

    if args.cmd == "init":
        print("initialised %s" % init_board())
        return 0

    root = find_root()
    try:
        if args.cmd == "new":
            print(create_ticket(root, args.title, args.desc, args.column, args.owner))
        elif args.cmd == "list":
            rows = list_tickets(root, args.column)
            if args.json:
                print(json.dumps([ticket_to_dict(c, t) for c, t in rows], indent=2))
            else:
                _print_rows(rows)
        elif args.cmd == "show":
            col, path = find_ticket(root, args.id)
            ticket = load_ticket(path)
            if args.json:
                print(json.dumps(ticket_to_dict(col, ticket), indent=2))
            else:
                print("[%s] %s" % (col, path))
                print(render_ticket(ticket))
        elif args.cmd == "take":
            take_ticket(root, args.id, args.owner)
            print("%s -> doing (@%s)" % (validate_id(args.id), args.owner))
        elif args.cmd == "move":
            move_ticket(root, args.id, args.column)
            print("%s -> %s" % (validate_id(args.id), args.column))
        elif args.cmd == "comment":
            add_comment(root, args.id, args.body, args.by)
            print("comment added to %s" % validate_id(args.id))
        elif args.cmd == "watch":
            _, seen = watch_once(root, args.column, {})
            print("watching %s/%s" % (BOARD_DIR, args.column), flush=True)
            while True:
                changed, seen = watch_once(root, args.column, seen)
                for tid in changed:
                    print("%s %s" % (args.column, tid), flush=True)
                time.sleep(args.interval)
        elif args.cmd == "serve":
            serve(root, args.port)
    except (KeyError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS — 38 tests

- [ ] **Step 5: Commit**

```bash
git add board.py tests/test_board.py
git commit -m "feat: CLI dispatch for all board commands"
```

---

### Task 8: Web UI

**Files:**
- Modify: `board.py`
- Modify: `tests/test_board.py`

**Interfaces:**
- Consumes: `list_tickets`, `create_ticket`, `move_ticket`, `add_comment`, `COLUMNS`
- Produces: `render_board_html(root: str) -> str`; `serve(root: str, port: int = 8899) -> None` (replaces the Task 7 stub)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_board.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -v`
Expected: FAIL — `AttributeError: module 'board' has no attribute 'render_board_html'`

- [ ] **Step 3: Write minimal implementation**

Delete the Task 7 `serve` stub, then add:

```python
# add to board.py
import html
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

PAGE_CSS = """
body{font:14px system-ui,sans-serif;margin:0;padding:16px;background:#f6f7f9;color:#111}
h1{font-size:18px;margin:0 0 12px}
.cols{display:flex;gap:12px;align-items:flex-start;overflow-x:auto}
.col{background:#fff;border:1px solid #dfe3e8;border-radius:8px;padding:10px;min-width:230px;flex:1}
.col h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:#667;margin:0 0 8px}
.t{border:1px solid #e5e8ec;border-radius:6px;padding:8px;margin-bottom:8px;background:#fcfcfd}
.t .id{color:#889;font-size:12px}
.t .own{color:#3a6;font-size:12px}
.meta{color:#889;font-size:12px}
form.inline{display:inline}
button{font:12px system-ui;padding:2px 6px;margin:1px 0;cursor:pointer}
.add{margin-top:16px;background:#fff;border:1px solid #dfe3e8;border-radius:8px;padding:12px;max-width:520px}
input,textarea{width:100%;padding:6px;margin:4px 0;border:1px solid #ccd;border-radius:4px;font:13px system-ui}
"""


def render_board_html(root: str) -> str:
    esc = html.escape
    cols_html = []
    for col in COLUMNS:
        cards = []
        for _, t in list_tickets(root, col):
            moves = "".join(
                '<form class="inline" method="post" action="/move">'
                '<input type="hidden" name="id" value="%s">'
                '<input type="hidden" name="column" value="%s">'
                "<button>%s</button></form>" % (esc(t.id), esc(c), esc(c))
                for c in COLUMNS if c != col
            )
            owner = '<span class="own">@%s</span>' % esc(t.owner) if t.owner else ""
            cards.append(
                '<div class="t"><span class="id">%s</span> %s %s'
                '<div class="meta">%d comment(s)</div><div>%s</div></div>'
                % (esc(t.id), esc(t.title), owner, len(t.comments), moves)
            )
        cols_html.append(
            '<div class="col"><h2>%s (%d)</h2>%s</div>'
            % (esc(col), len(cards), "".join(cards) or '<div class="meta">empty</div>')
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta http-equiv='refresh' content='3'>"
        "<title>agent-board</title><style>%s</style></head><body>"
        "<h1>agent-board</h1><div class='cols'>%s</div>"
        "<div class='add'><form method='post' action='/new'>"
        "<input name='title' placeholder='Ticket title' required>"
        "<textarea name='desc' rows='3' placeholder='Description'></textarea>"
        "<button>Add ticket</button></form></div>"
        "</body></html>" % (PAGE_CSS, "".join(cols_html))
    )


def _make_handler(root: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if urlparse(self.path).path != "/":
                self.send_error(404)
                return
            body = render_board_html(root).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            fields = parse_qs(self.rfile.read(length).decode("utf-8"))
            path = urlparse(self.path).path
            try:
                if path == "/new":
                    title = (fields.get("title") or [""])[0].strip()
                    if title:
                        create_ticket(root, title, (fields.get("desc") or [""])[0])
                elif path == "/move":
                    move_ticket(root, (fields.get("id") or [""])[0],
                                (fields.get("column") or [""])[0])
                elif path == "/comment":
                    add_comment(root, (fields.get("id") or [""])[0],
                                (fields.get("body") or [""])[0],
                                (fields.get("by") or ["human"])[0])
                else:
                    self.send_error(404)
                    return
            except (KeyError, ValueError) as exc:
                self.send_error(400, str(exc))
                return
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()

    return Handler


def serve(root: str, port: int = 8899) -> None:
    server = HTTPServer(("127.0.0.1", port), _make_handler(root))
    print("agent-board on http://127.0.0.1:%d  (ctrl-c to stop)" % port, flush=True)
    server.serve_forever()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS — 42 tests

- [ ] **Step 5: Commit**

```bash
git add board.py tests/test_board.py
git commit -m "feat: local web UI as a pure projection over the filesystem"
```

---

### Task 9: Packaging — shim and README

**Files:**
- Create: `board`
- Create: `README.md`

**Interfaces:**
- Consumes: `board.py`
- Produces: an executable `board` suitable for symlinking onto `PATH`

- [ ] **Step 1: Run the smoke test to verify it fails**

Run: `cd /tmp && rm -rf bt && mkdir bt && cd bt && ~/Source/bronhills/agent-board/board init`
Expected: FAIL — `no such file or directory`

- [ ] **Step 2: Create the shim**

```bash
cat > board <<'SH'
#!/usr/bin/env bash
# Resolve through symlinks so this works from /usr/local/bin.
src="${BASH_SOURCE[0]}"
while [ -L "$src" ]; do
  dir="$(cd -P "$(dirname "$src")" && pwd)"
  src="$(readlink "$src")"
  [[ "$src" != /* ]] && src="$dir/$src"
done
dir="$(cd -P "$(dirname "$src")" && pwd)"
exec python3 "$dir/board.py" "$@"
SH
chmod +x board
```

- [ ] **Step 3: Write the README**

````markdown
# agent-board

A file-backed kanban board for coordinating several coding agents with a human in
the loop. Directories are columns, tickets are markdown files, git is the history.

No dependencies. Python 3.9+.

## Install

```bash
git clone git@github.com:jharjadi/agent-board.git ~/Source/agent-board
ln -s ~/Source/agent-board/board /usr/local/bin/board
```

## Use

```bash
cd my-project
board init                              # creates .agent-board/
board new "Fix login redirect loop" --desc "401 doesn't clear the cookie"
board list
board take 1 --owner codex              # -> doing/
board comment 1 "Fixed in a1b2c3d" --by claude
board move 1 review
board serve                             # http://127.0.0.1:8899
```

## Wiring an agent

Give an agent a standing role and a column to watch:

> "You are the reviewer. Watch `.agent-board/review/`. When a ticket appears, read
> it, review the branch named in its frontmatter, comment with `board comment`, then
> move it to `done` or `blocked`."

Auto-nudge it from cmux so you never have to say "check the board":

```bash
board watch review | while read -r line; do
  cmux send --surface "$SURFACE" "Board changed: $line. Run: board list review"
  cmux send-key --surface "$SURFACE" Enter
done
```

Keep nudges content-free — the payload belongs in the ticket, which is inspectable
and version-controlled. An agent running with `--yolo` or
`--dangerously-skip-permissions` cannot tell injected keystrokes from the human
typing, so instructions must never travel over `cmux send`.

## Rules

- The **directory is the only truth**. There is no `status` field.
- The board carries coordination; **git carries code**. Diffs never go in tickets.
- Give each agent its own **git worktree** so they cannot corrupt each other.

## Design

See `docs/superpowers/specs/2026-09-04-agent-board-design.md`.
````

- [ ] **Step 4: Run the smoke test to verify it passes**

Run: `cd /tmp/bt && ~/Source/bronhills/agent-board/board init && ~/Source/bronhills/agent-board/board new "smoke test" && ~/Source/bronhills/agent-board/board list`
Expected: prints `001  todo     smoke test`

- [ ] **Step 5: Commit and push**

```bash
git add board README.md
git commit -m "feat: board shim and README"
git push
```

---

## Self-Review

**Spec coverage.** Storage layout → Task 2. Ticket format and markdown-over-JSON → Task 1 (round-trip test). Directory-is-truth (no `status` field) → Task 1 `render_ticket`. CLI surface → Tasks 3-7. Push-not-poll watcher → Task 6. Web UI plus the refresh-reconstructs-from-disk invariant → Task 8. Single copyable file → Task 9. Path-traversal rejection → Task 2. Concurrent `new` → Task 3. Concurrent `comment` → Task 5. Atomic move → Task 5.

**Two spec items deliberately not built as code.** Git worktrees and cmux nudge wiring are operational practice, documented in the README (Task 9). There is nothing for `board.py` to do about either, and inventing commands for them would be scope creep.

**Type consistency.** `find_ticket` returns `(column, path)` in Tasks 4, 5, 7. `list_tickets` returns `List[Tuple[str, Ticket]]` and is unpacked that way in Tasks 4, 7, 8. `watch_once(root, column, seen)` returns `(changed, snapshot)` in Tasks 6 and 7. `validate_id` returns a zero-padded id everywhere, so `board take 7` and `board take 007` behave identically. `Ticket` is only ever constructed with keywords, so field order is not load-bearing.

**Known ordering wrinkle.** Task 7's `main` dispatches `serve`, which Task 8 defines. Task 7 states the temporary stub explicitly rather than leaving a confusing failure.
