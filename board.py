#!/usr/bin/env python3
"""agent-board - a file-backed kanban board for coordinating coding agents."""
import argparse
import contextlib
import errno
import fcntl
import glob
import html
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

COLUMNS = ("todo", "doing", "review", "blocked", "done")
THREADS_DIR = "threads"
FM_DELIM = "---"
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

@dataclass
class Ticket:
    id: str
    title: str
    created: str
    description: str = ""
    owner: str | None = None
    branch: str | None = None
    ticket: str | None = None
    comments: list[Comment] = field(default_factory=list)


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
        seen.add(key)
        if key == "to" and value:
            to = value
        elif key == "ask" and not value:
            ask = True
        elif key == "re":
            nums: list[int] = []
            for tok in value.split(","):
                tok = tok.strip()
                if not re.fullmatch(r"[0-9]{1,9}", tok) or int(tok) < 1:
                    nums = []
                    break
                nums.append(int(tok))
            refs = nums
        elif key == "commit" and value:
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


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
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
    return Ticket(
        id=meta["id"],
        title=meta["title"],
        created=meta["created"],
        description="\n".join(desc).strip(),
        owner=meta.get("owner") or None,
        branch=meta.get("branch") or None,
        ticket=meta.get("ticket") or None,
        comments=comments,
    )


def render_ticket(t: Ticket) -> str:
    out = [FM_DELIM, 'id: "%s"' % t.id, "title: %s" % t.title]
    if t.owner:
        out.append("owner: %s" % t.owner)
    out.append("created: %s" % t.created)
    if t.branch:
        out.append("branch: %s" % t.branch)
    if t.ticket:
        out.append('ticket: "%s"' % t.ticket)
    out.append(FM_DELIM)
    text = "\n".join(out) + "\n\n" + t.description.strip() + "\n"
    for c in t.comments:
        text += "\n%s\n%s\n" % (render_comment_header(c), c.body.strip())
    return text


BOARD_DIR = ".agent-board"
ID_RE = re.compile(r"^[0-9]{1,6}\Z")


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


def is_thread(column: str) -> bool:
    return column == THREADS_DIR

def _refuse_thread(column: str, tid: str, verb: str) -> None:
    if is_thread(column):
        raise ValueError("%s is a thread; threads have no column and no owner, so it cannot be %s"
                         % (validate_id(tid), verb))

def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60] or "ticket"


def sanitize_scalar(value: str) -> str:
    """Frontmatter values must be single-line. Collapse control chars to spaces."""
    cleaned = "".join(" " if (ch == "\n" or ch == "\r" or ord(ch) < 32) else ch for ch in value)
    return " ".join(cleaned.split()).strip()


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


LOCK_NAME = ".lock"
ROOT_ENV = "AGENT_BOARD_ROOT"


@contextlib.contextmanager
def board_lock(root: str):
    """Serialise every mutation on one kernel-held lock.

    fcntl.flock is released by the kernel when the process dies, so there is no
    stale lock to expire and no pid file to reason about. The lock file is
    permanent and never replaced — unlinking it would leave waiters holding a
    different inode, and the lock would silently stop working.

    Advisory only: anything that writes without taking it (a hand edit, another
    tool) is not protected. That boundary is documented, not defended against.
    """
    os.makedirs(root, exist_ok=True)
    fd = os.open(os.path.join(root, LOCK_NAME), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)          # closing releases the lock


def init_board(base: str | None = None) -> str:
    base = base or os.getcwd()
    root = os.path.join(base, BOARD_DIR)
    for col in COLUMNS:
        os.makedirs(os.path.join(root, col), exist_ok=True)
    os.makedirs(os.path.join(root, THREADS_DIR), exist_ok=True)
    # Create the lock up front so it has one stable inode for the board's life,
    # and keep it out of git for boards that are committed.
    lock = os.path.join(root, LOCK_NAME)
    if not os.path.exists(lock):
        os.close(os.open(lock, os.O_CREAT | os.O_RDWR, 0o644))
    ignore = os.path.join(root, ".gitignore")
    if not os.path.exists(ignore):
        atomic_write(ignore, "%s\n" % LOCK_NAME)
    return root


AGENTS_BEGIN = "<!-- agent-board:begin -->"
AGENTS_END = "<!-- agent-board:end -->"


def agents_block() -> str:
    """The instructions an agent needs to discover and use this board.

    Deliberately role-neutral: every agent in the project reads the same file,
    so it cannot name one agent's column.
    """
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

The inbox also shows answers to your asks until you post again in that file.
Reading the file alone does not acknowledge an answer.

After you post, nudge the recipient yourself if you know its pane, for example
with `cmux send`. The board never notifies anyone, and the nudge must carry no
content: the message is in the file.
%s""" % (AGENTS_BEGIN, AGENTS_END)

def _upsert_block(path: str, block: str) -> None:
    """Insert or replace the delimited block, preserving everything else."""
    existing = ""
    if os.path.exists(path) and not os.path.islink(path):
        with open(path, encoding="utf-8") as fh:
            existing = fh.read()
    if AGENTS_BEGIN in existing and AGENTS_END in existing:
        head = existing.split(AGENTS_BEGIN)[0]
        tail = existing.split(AGENTS_END, 1)[1]
        text = head + block + tail
    elif existing.strip():
        text = existing.rstrip("\n") + "\n\n" + block + "\n"
    else:
        text = block + "\n"
    atomic_write(path, text)


def write_agents_doc(base: str | None = None) -> list[str]:
    """Teach agents that this board exists, whatever launches them.

    AGENTS.md is read by Codex and OpenCode, CLAUDE.md by Claude Code — from a
    VS Code extension, a cmux terminal or a bare shell alike. Writing both is
    what makes the board discoverable without the user having to say so.
    """
    base = base or os.getcwd()
    block = agents_block()
    touched = []

    agents_path = os.path.join(base, "AGENTS.md")
    _upsert_block(agents_path, block)
    touched.append(agents_path)

    claude_path = os.path.join(base, "CLAUDE.md")
    if not os.path.lexists(claude_path):
        os.symlink("AGENTS.md", claude_path)
        touched.append(claude_path)
    elif not os.path.islink(claude_path):
        # A real file with its own content — append rather than clobber it.
        _upsert_block(claude_path, block)
        touched.append(claude_path)
    return touched


def find_root(start: str | None = None) -> str:
    override = os.environ.get(ROOT_ENV)
    if override:
        # Explicit wins over discovery, and a bad value is an error, never a
        # silent fallback to some other board found by walking upward.
        if not os.path.isdir(override):
            sys.exit("%s=%s is not a directory" % (ROOT_ENV, override))
        return os.path.abspath(override)
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


MAX_ID_ATTEMPTS = 5
MAX_BODY_BYTES = 1_000_000


def _all_ticket_paths(root: str) -> list[str]:
    paths = list(glob.glob(os.path.join(root, "*.md")))
    for directory in COLUMNS + (THREADS_DIR,):
        paths.extend(glob.glob(os.path.join(root, directory, "*.md")))
    return paths

def next_id(root: str) -> str:
    highest = 0
    for path in _all_ticket_paths(root):
        stem = os.path.splitext(os.path.basename(path))[0]
        head = stem.split("-", 1)[0]
        if head.isdigit():
            highest = max(highest, int(head))
    return str(highest + 1).zfill(3)


def _create_locked(root: str, title: str, description: str, dest_dir: str,
                   owner: str | None = None, ticket_ref: str | None = None,
                   opening: Comment | None = None) -> tuple[str, str]:
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
                            description=description, owner=owner, ticket=ticket_ref,
                            comments=[opening] if opening is not None else [])
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
            col, _ = find_ticket(root, ticket_ref)
            if is_thread(col):
                raise ValueError("--ticket must refer to a ticket, not a thread")
        tid, _ = _create_locked(root, title, "", THREADS_DIR,
                                ticket_ref=ticket_ref, opening=comment)
    return tid


def list_threads(root: str) -> list[Ticket]:
    """Every thread, newest activity first. Activity is the last message's
    time, or creation for a thread with none, which cannot happen through the
    CLI but can through a hand-made file."""
    rows = [load_ticket(p) for p in sorted(glob.glob(os.path.join(root, THREADS_DIR, "*.md")))]
    rows.sort(key=lambda t: (t.comments[-1].at if t.comments else t.created, int(t.id)),
              reverse=True)
    return rows


def load_ticket(path: str) -> Ticket:
    with open(path, encoding="utf-8") as fh:
        return parse_ticket(fh.read())


def find_ticket(root: str, tid: str) -> tuple[str, str]:
    """Locate a ticket by id. Exactly one file may carry an id; two is an error,
    not a coin toss, because every mutation would otherwise act on whichever
    sorted first and leave the other as a silent twin."""
    tid = validate_id(tid)
    found: list[tuple[str, str]] = []
    for col in COLUMNS + (THREADS_DIR,):
        for pattern in ("%s-*.md" % tid, "%s.md" % tid):
            for path in sorted(glob.glob(os.path.join(root, col, pattern))):
                found.append((col, path))
    if not found:
        raise KeyError("no ticket with id %s" % tid)
    if len(found) > 1:
        raise ValueError("id %s matches %d files: %s"
                         % (tid, len(found), ", ".join(p for _, p in found)))
    return found[0]

def list_tickets(root: str, column: str | None = None) -> list[tuple[str, Ticket]]:
    cols = (validate_column(column),) if column else COLUMNS
    rows = []
    for col in cols:
        for path in sorted(glob.glob(os.path.join(root, col, "*.md"))):
            rows.append((col, load_ticket(path)))
    rows.sort(key=lambda row: int(row[1].id))
    return rows


def ticket_to_dict(column: str, t: Ticket) -> dict:
    data = asdict(t)
    data["column"] = column
    data["kind"] = "thread" if is_thread(column) else "ticket"
    for n, c in enumerate(data["comments"], start=1):
        c["n"] = n
    return data

def _move_locked(root: str, tid: str, column: str) -> str:
    """Caller must already hold the board lock."""
    column = validate_column(column)
    col, path = find_ticket(root, tid)
    _refuse_thread(col, tid, "moved")
    dest = os.path.join(root, column, os.path.basename(path))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    os.replace(path, dest)
    return dest


def move_ticket(root: str, tid: str, column: str) -> str:
    with board_lock(root):
        return _move_locked(root, tid, column)


def take_ticket(root: str, tid: str, owner: str, expect: str | None = None) -> str:
    """Claim a ticket.

    With `expect`, the claim is a guarded transition: the ticket must currently
    be in that column or the claim is refused. The lock alone does not prevent a
    second claim — it only serialises two claims that both otherwise succeed —
    so the condition is a separate check sharing the same critical section.

    The owner field is never consulted for this. It is an advisory hint, and
    treating it as authority would quietly make the board care who agents are.
    """
    with board_lock(root):
        col, path = find_ticket(root, tid)
        _refuse_thread(col, tid, "taken")
        if expect is not None and col != validate_column(expect):
            raise ValueError(
                "ticket %s is in %r, not %r — already claimed?"
                % (validate_id(tid), col, expect)
            )
        ticket = load_ticket(path)
        ticket.owner = sanitize_scalar(owner) or None
        atomic_write(path, render_ticket(ticket))
        return _move_locked(root, tid, "doing")


def set_owner(root: str, tid: str, owner: str) -> None:
    """Set the owner hint without moving the ticket.

    Purely advisory: the board routes nothing and does not know which agents
    exist. Pass an empty string to clear it. Resolved and read inside the lock,
    because a path or a body read before acquiring it may already be stale.
    """
    with board_lock(root):
        col, path = find_ticket(root, tid)
        _refuse_thread(col, tid, "assigned")
        ticket = load_ticket(path)
        ticket.owner = sanitize_scalar(owner) or None
        atomic_write(path, render_ticket(ticket))


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
        if commit is None:
            raise ValueError("--commit must contain a value")
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
            if written == 0:
                raise OSError("could not append the message")
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

def column_snapshot(root: str, column: str) -> dict[str, float]:
    column = validate_column(column)
    snap = {}
    for path in glob.glob(os.path.join(root, column, "*.md")):
        stem = os.path.splitext(os.path.basename(path))[0]
        head = stem.split("-", 1)[0]
        if head.isdigit():
            snap[head.zfill(3)] = os.path.getmtime(path)
    return snap


def watch_once(root: str, column: str, seen: dict[str, float]) -> tuple[list[str], dict[str, float]]:
    current = column_snapshot(root, column)
    changed = sorted((tid for tid, mtime in current.items() if seen.get(tid) != mtime), key=int)
    return changed, current

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


def _print_rows(rows) -> None:
    if not rows:
        print("(empty)")
        return
    for col, t in rows:
        owner = " @%s" % t.owner if t.owner else ""
        print("%s  %-8s %s%s" % (t.id, col, t.title, owner))


COLUMN_ACCENT = {
    "todo": "#5e6c84",
    "doing": "#0052cc",
    "review": "#ff8b00",
    "blocked": "#de350b",
    "done": "#00875a",
}

PAGE_CSS = """
*{box-sizing:border-box}
body{margin:0;padding:20px 24px;background:#f4f5f7;color:#172b4d;
 font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
h1{font-size:16px;font-weight:600;margin:0 0 3px;letter-spacing:-.01em}
.sub{color:#6b778c;font-size:12px;margin:0 0 18px}
.cols{display:flex;gap:10px;align-items:flex-start;padding-bottom:8px;overflow-x:auto}
.col{background:#ebecf0;border-radius:4px;padding:8px;flex:1 1 0;min-width:200px;min-height:110px}
.col h2{display:flex;align-items:center;gap:7px;font-size:11px;font-weight:700;
 text-transform:uppercase;letter-spacing:.08em;color:#5e6c84;margin:4px 6px 10px}
.dot{width:8px;height:8px;border-radius:50%;flex:0 0 auto}
.n{margin-left:auto;color:#5e6c84;font-weight:600}
.t{background:#fff;border-radius:3px;padding:10px 12px;margin-bottom:8px;
 box-shadow:0 1px 2px rgba(9,30,66,.2);min-width:0;overflow-wrap:anywhere}
.t:hover{box-shadow:0 2px 6px rgba(9,30,66,.25)}
.title{font-size:14px;font-weight:500;margin-bottom:5px;word-wrap:break-word}
.desc{color:#5e6c84;font-size:12.5px;margin:0 0 6px;white-space:pre-wrap;word-wrap:break-word}
.meta{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:7px}
.id{font:600 11px ui-monospace,SFMono-Regular,Menlo,monospace;color:#5e6c84;
 background:#f4f5f7;border-radius:3px;padding:1px 6px}
.own{background:#deebff;color:#0747a6;border-radius:10px;padding:1px 8px;font-size:11px;font-weight:600}
.cnt{color:#6b778c;font-size:11px}
.comments{margin-top:9px;border-top:1px solid #f4f5f7;padding-top:8px;max-height:240px;overflow-y:auto}
.c{margin-bottom:8px}
.c:last-child{margin-bottom:0}
.c .who{font-size:11px;font-weight:600}
.c .when{font-size:11px;color:#6b778c;margin-left:6px}
.c .body{font-size:12.5px;color:#42526e;white-space:pre-wrap;word-wrap:break-word;margin-top:1px}
details{margin-top:8px}
summary{cursor:pointer;color:#6b778c;font-size:11px;list-style:none;user-select:none}
summary::-webkit-details-marker{display:none}
summary:hover{color:#0052cc}
.acts{margin-top:7px;display:flex;flex-direction:column;gap:6px}
.pills{display:flex;gap:4px;flex-wrap:wrap}
form.inline{display:flex;gap:4px}
button{font:500 11px inherit;padding:3px 8px;border:1px solid #dfe1e6;background:#fafbfc;
 color:#42526e;border-radius:3px;cursor:pointer}
button:hover{background:#f4f5f7;border-color:#c1c7d0}
input,textarea{font:inherit;font-size:12px;padding:4px 8px;border:1px solid #dfe1e6;
 background:#fafbfc;border-radius:3px;color:#172b4d;min-width:0;flex:1}
input:focus,textarea:focus{outline:none;background:#fff;border-color:#4c9aff;
 box-shadow:0 0 0 2px rgba(76,154,255,.25)}
.add{flex:1 1 300px;min-width:0;margin-top:18px;background:#fff;border-radius:4px;padding:14px;max-width:420px;
 box-shadow:0 1px 2px rgba(9,30,66,.2)}
.add h3{margin:0 0 9px;font-size:11px;font-weight:700;color:#5e6c84;
 text-transform:uppercase;letter-spacing:.08em}
.add input,.add textarea{width:100%;margin-bottom:8px;display:block}
.add textarea{resize:vertical}
.empty{color:#8993a4;font-size:12px;padding:8px 6px}
.num{font:600 11px ui-monospace,SFMono-Regular,Menlo,monospace;color:#8993a4;margin-right:6px}
.bg{display:inline-block;font-size:10.5px;font-weight:600;color:#42526e;background:#f4f5f7;
 border-radius:3px;padding:0 5px;margin-left:5px;vertical-align:1px}
.bg.ask{background:#fff0b3;color:#7f5f01}
.bg.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-weight:500}
.c.pend{border-left:3px solid #ff8b00;padding-left:7px}
.pending{background:#ffebe6;color:#bf2600;border-radius:10px;padding:1px 8px;font-size:11px;font-weight:600}
.about{color:#6b778c;font-size:11px}
.wait{background:#fffae6;border:1px solid #ffe380;border-radius:4px;padding:10px 14px;margin:0 0 16px}
.wait h3{margin:0 0 6px;font-size:11px;font-weight:700;color:#7f5f01;text-transform:uppercase;letter-spacing:.08em}
.wait ul{margin:0;padding-left:18px;font-size:12.5px}
.wait li{margin:2px 0;overflow-wrap:anywhere}
.wait a{color:#0052cc;text-decoration:none;font:600 11px ui-monospace,SFMono-Regular,Menlo,monospace}
.sec{display:flex;align-items:center;gap:7px;font-size:11px;font-weight:700;text-transform:uppercase;
 letter-spacing:.08em;color:#5e6c84;margin:22px 0 10px}
.threads{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,360px),1fr));gap:10px;align-items:start}
.threads .t{margin-bottom:0}
.sm{flex:0 0 74px}
label.ask{display:flex;align-items:center;gap:3px;font-size:11px;color:#42526e;white-space:nowrap}
label.ask input{flex:0 0 auto;min-width:0}
.adds{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-start}
.add .row{display:flex;gap:6px;margin-bottom:8px;flex-wrap:wrap}
.add .row input{margin-bottom:0;width:auto}
.add .row>input{flex:1 1 100px}
label.ask input{width:auto;margin:0}
.reply-form{display:flex;gap:6px;flex-wrap:wrap}
.reply-form textarea{flex:1 1 100%;width:100%;resize:vertical}
.reply-form>input:not([type=hidden]){flex:1 1 100px;width:100%;min-width:0}
.reply-form label.ask{flex:1 1 100px}
.t:target{outline:2px solid #4c9aff;outline-offset:2px}
@media(max-width:850px){.threads{grid-template-columns:1fr}}
@media(max-width:480px){body{padding:16px}.add{max-width:none}}
"""


def _project_label(root: str) -> str:
    """Name the project, not the path to its board directory."""
    project = os.path.dirname(os.path.abspath(root))
    home = os.path.expanduser("~")
    if project.startswith(home):
        return "~" + project[len(home):]
    return os.path.basename(project) or project


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
    return (
        '<form class="reply-form" method="post" action="/comment">'
        '<input type="hidden" name="id" value="%s">'
        '<textarea name="body" rows="2" placeholder="Add a message" '
        'aria-label="Message" required></textarea>'
        '<input name="by" placeholder="From" aria-label="From" value="human" required>'
        '<input name="to" placeholder="To (optional)" aria-label="To (optional)">'
        '<input name="re" placeholder="Reply to # (e.g. 1,2)" aria-label="Reply to message numbers">'
        '<label class="ask"><input type="checkbox" name="ask" value="1">Request reply</label>'
        '<button>Send</button></form>' % html.escape(tid)
    )


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
        "if(document.querySelector('details[open]'))return;"
        "var f=document.querySelectorAll('input:not([type=hidden]),textarea,select');"
        "for(var i=0;i<f.length;i++){"
        "if(f[i].type==='checkbox'||f[i].type==='radio'){"
        "if(f[i].checked!==f[i].defaultChecked)return;"
        "}else if(f[i].value!==f[i].defaultValue)return;}"
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
        "<div class='row'><input name='by' placeholder='From' aria-label='From' value='human' required>"
        "<input name='to' placeholder='To (optional)' aria-label='To (optional)'>"
        "<label class='ask'><input type='checkbox' name='ask' value='1'>Request reply</label></div>"
        "<button>Start thread</button></form></div>"
        "</div>"
        "</body></html>"
        % (PAGE_CSS, total, "" if total == 1 else "s", len(threads), "" if len(threads) == 1 else "s",
           esc(_project_label(root)), _render_waiting(items), "".join(cols_html),
           len(threads), thread_cards)
    )

def _make_handler(root: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if urlparse(self.path).path != "/":
                self.send_error(404)
                return
            try:
                body = render_board_html(root).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (KeyError, ValueError, RuntimeError, OSError) as exc:
                self.send_error(500, str(exc))
                return

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                raw_len = self.headers.get("Content-Length") or "0"
                length = int(raw_len)
                if length < 0 or length > MAX_BODY_BYTES:
                    raise ValueError("bad Content-Length: %s" % raw_len)
                fields = parse_qs(self.rfile.read(length).decode("utf-8"))
                if path == "/new":
                    title = (fields.get("title") or [""])[0].strip()
                    if title:
                        create_ticket(root, title, (fields.get("desc") or [""])[0])
                elif path == "/move":
                    move_ticket(root, (fields.get("id") or [""])[0],
                                (fields.get("column") or [""])[0])
                elif path == "/assign":
                    set_owner(root, (fields.get("id") or [""])[0],
                              (fields.get("owner") or [""])[0])
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
                else:
                    self.send_error(404)
                    return
            except (KeyError, ValueError, RuntimeError, OSError) as exc:
                self.send_error(400, str(exc))
                return
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()

    return Handler


def serve(root: str, port: int = 8899) -> None:
    try:
        server = HTTPServer(("127.0.0.1", port), _make_handler(root))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise SystemExit(
                "port %d is already in use — another board may be running.\n"
                "Try:  board serve --port %d" % (port, port + 1)
            )
        raise
    print("agent-board on http://127.0.0.1:%d  (ctrl-c to stop)" % port, flush=True)
    server.serve_forever()


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
        if not re.fullmatch(r"[0-9]{1,9}", tok):
            raise ValueError("--re expects message numbers like 3 or 1,2,3, not %r" % tok)
        refs.append(int(tok))
    return refs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="board",
                                     description="file-backed kanban for coding agents")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("init")
    p.add_argument("--no-agents", action="store_true",
                   help="only create .agent-board/; do not touch AGENTS.md or CLAUDE.md")

    p = sub.add_parser("new")
    p.add_argument("title")
    p.add_argument("--desc", default="")
    p.add_argument("--column", default="todo")
    p.add_argument("--owner", default=None)

    p = sub.add_parser("list")
    p.add_argument("column", nargs="?", default=None)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("take")
    p.add_argument("id")
    p.add_argument("--owner", required=True)
    p.add_argument("--from", dest="expect", default=None, metavar="COLUMN",
                   help="only claim if the ticket is still in COLUMN "
                        "(refuses a ticket someone else already took)")

    p = sub.add_parser("move")
    p.add_argument("id")
    p.add_argument("column")

    p = sub.add_parser("assign")
    p.add_argument("id")
    p.add_argument("owner", help="name or role; pass '' to clear the hint")

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
        if not args.no_agents:
            for path in write_agents_doc():
                print("wrote %s" % path)
        return 0

    root = find_root()
    if os.environ.get(ROOT_ENV):
        print("board: %s (via %s)" % (root, ROOT_ENV), file=sys.stderr)
    try:
        if args.cmd == "new":
            print(create_ticket(root, args.title, args.desc, args.column, args.owner))
        elif args.cmd == "list":
            rows = list_tickets(root, args.column)
            if args.json:
                print(json.dumps([ticket_to_dict(c, t) for c, t in rows], indent=2))
            else:
                _print_rows(rows)
        elif args.cmd == "take":
            take_ticket(root, args.id, args.owner, expect=args.expect)
            print("%s -> doing (@%s)" % (validate_id(args.id), args.owner))
        elif args.cmd == "move":
            move_ticket(root, args.id, args.column)
            print("%s -> %s" % (validate_id(args.id), args.column))
        elif args.cmd == "assign":
            set_owner(root, args.id, args.owner)
            print("%s owner -> %s" % (validate_id(args.id), args.owner or "(cleared)"))
        elif args.cmd == "show":
            with board_lock(root):
                col, path = find_ticket(root, args.id)
                ticket = load_ticket(path)
            if args.last is not None and args.last < 0:
                raise ValueError("--last must be zero or greater")
            total = len(ticket.comments)
            start = max(0, total - args.last) if args.last is not None else 0
            if args.json:
                data = ticket_to_dict(col, ticket)
                data["comments"] = data["comments"][start:]
                print(json.dumps(data, indent=2))
            else:
                print("[%s] %s" % (col, path))
                print(render_ticket(replace(ticket, comments=[])).rstrip("\n"))
                if start:
                    showing = ("showing %d-%d of %d" % (start + 1, total, total)
                               if start < total else "showing no messages")
                    print("\n(%d earlier message%s omitted; %s)"
                          % (start, "" if start == 1 else "s", showing))
                for n, c in enumerate(ticket.comments[start:], start=start + 1):
                    print("\n[#%d]\n%s\n%s" % (n, render_comment_header(c), c.body))
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
    except (KeyError, ValueError, RuntimeError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
