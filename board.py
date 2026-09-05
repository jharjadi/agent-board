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
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

COLUMNS = ("todo", "doing", "review", "blocked", "done")
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

This project uses agent-board. The board lives in `.agent-board/`; each column
is a directory and each ticket is a markdown file.

    board list                        see the whole board
    board list todo                   see one column
    board show 7                      read a ticket, with its comments
    board take 7 --owner <you>        claim it (moves to doing/)
    board comment 7 "..." --by <you>  report back on it
    board move 7 review               hand it on

Use your own name or role as `<you>` — whatever the user calls you. If the user
has not told you which column is yours, ask, or take the top of `todo`. Read the
ticket before starting, and report with `board comment` rather than only in chat,
so the next agent sees what you did.
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
    for col in COLUMNS:
        paths.extend(glob.glob(os.path.join(root, col, "*.md")))
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

def load_ticket(path: str) -> Ticket:
    with open(path, encoding="utf-8") as fh:
        return parse_ticket(fh.read())


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
    return data


def _move_locked(root: str, tid: str, column: str) -> str:
    """Caller must already hold the board lock."""
    column = validate_column(column)
    _, path = find_ticket(root, tid)
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
        _, path = find_ticket(root, tid)
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
.cols{display:flex;gap:10px;align-items:flex-start;padding-bottom:8px}
.col{background:#ebecf0;border-radius:4px;padding:8px;flex:1 1 0;min-width:200px;min-height:110px}
.col h2{display:flex;align-items:center;gap:7px;font-size:11px;font-weight:700;
 text-transform:uppercase;letter-spacing:.08em;color:#5e6c84;margin:4px 6px 10px}
.dot{width:8px;height:8px;border-radius:50%;flex:0 0 auto}
.n{margin-left:auto;color:#5e6c84;font-weight:600}
.t{background:#fff;border-radius:3px;padding:10px 12px;margin-bottom:8px;
 box-shadow:0 1px 2px rgba(9,30,66,.2)}
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
.add{margin-top:18px;background:#fff;border-radius:4px;padding:14px;max-width:420px;
 box-shadow:0 1px 2px rgba(9,30,66,.2)}
.add h3{margin:0 0 9px;font-size:11px;font-weight:700;color:#5e6c84;
 text-transform:uppercase;letter-spacing:.08em}
.add input,.add textarea{width:100%;margin-bottom:8px;display:block}
.add textarea{resize:vertical}
.empty{color:#8993a4;font-size:12px;padding:8px 6px}
"""


def _project_label(root: str) -> str:
    """Name the project, not the path to its board directory."""
    project = os.path.dirname(os.path.abspath(root))
    home = os.path.expanduser("~")
    if project.startswith(home):
        return "~" + project[len(home):]
    return os.path.basename(project) or project


def _render_card(t, col: str) -> str:
    """One ticket card: identity, body, comments, then actions behind a toggle."""
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
    comment_form = (
        '<form class="inline" method="post" action="/comment">'
        '<input type="hidden" name="id" value="%s">'
        '<input name="body" placeholder="Add a comment" required>'
        "<button>Send</button></form>" % esc(t.id)
    )

    desc = '<div class="desc">%s</div>' % esc(t.description) if t.description else ""
    owner = '<span class="own">%s</span>' % esc(t.owner) if t.owner else ""
    count = ('<span class="cnt">%d comment%s</span>'
             % (len(t.comments), "" if len(t.comments) == 1 else "s")) if t.comments else ""

    comments = ""
    if t.comments:
        rows = "".join(
            '<div class="c"><span class="who">%s</span><span class="when">%s</span>'
            '<div class="body">%s</div></div>'
            % (esc(c.by), esc(c.at.replace("T", " ").rstrip("Z")), esc(c.body))
            for c in t.comments
        )
        comments = '<div class="comments">%s</div>' % rows

    return (
        '<div class="t">'
        '<div class="title">%s</div>%s'
        '<div class="meta"><span class="id">%s</span>%s%s</div>'
        "%s"
        '<details><summary>Actions</summary><div class="acts">'
        '<div class="pills">%s</div>%s%s</div></details>'
        "</div>"
        % (esc(t.title), desc, esc(t.id), owner, count,
           comments, moves, assign_form, comment_form)
    )


def render_board_html(root: str) -> str:
    esc = html.escape
    cols_html = []
    total = 0
    for col in COLUMNS:
        rows = list_tickets(root, col)
        total += len(rows)
        cards = "".join(_render_card(t, col) for _, t in rows)
        cols_html.append(
            '<div class="col"><h2><span class="dot" style="background:%s"></span>%s'
            '<span class="n">%d</span></h2>%s</div>'
            % (COLUMN_ACCENT.get(col, "#5e6c84"), esc(col), len(rows),
               cards or '<div class="empty">Nothing here</div>')
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        # A meta refresh reloads mid-typing and destroys whatever is in a form.
        # Poll instead, and skip the reload while the user is editing.
        "<script>setInterval(function(){"
        "var a=document.activeElement;"
        "if(a&&(a.tagName==='INPUT'||a.tagName==='TEXTAREA'))return;"
        "var f=document.querySelectorAll('input[type=text],input:not([type]),textarea');"
        "for(var i=0;i<f.length;i++){if(f[i].value.trim())return;}"
        "location.reload();},3000);</script>"
        "<title>agent-board</title><style>%s</style></head><body>"
        "<h1>agent-board</h1>"
        "<p class='sub'>%d ticket%s &middot; %s</p>"
        "<div class='cols'>%s</div>"
        "<div class='add'><h3>New ticket</h3><form method='post' action='/new'>"
        "<input name='title' placeholder='Title' required>"
        "<textarea name='desc' rows='3' placeholder='Description (optional)'></textarea>"
        "<button>Create</button></form></div>"
        "</body></html>"
        % (PAGE_CSS, total, "" if total == 1 else "s",
           esc(_project_label(root)), "".join(cols_html))
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
                                (fields.get("by") or ["human"])[0])
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

    p = sub.add_parser("show")
    p.add_argument("id")
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
        elif args.cmd == "show":
            col, path = find_ticket(root, args.id)
            ticket = load_ticket(path)
            if args.json:
                print(json.dumps(ticket_to_dict(col, ticket), indent=2))
            else:
                print("[%s] %s" % (col, path))
                print(render_ticket(ticket))
        elif args.cmd == "take":
            take_ticket(root, args.id, args.owner, expect=args.expect)
            print("%s -> doing (@%s)" % (validate_id(args.id), args.owner))
        elif args.cmd == "move":
            move_ticket(root, args.id, args.column)
            print("%s -> %s" % (validate_id(args.id), args.column))
        elif args.cmd == "assign":
            set_owner(root, args.id, args.owner)
            print("%s owner -> %s" % (validate_id(args.id), args.owner or "(cleared)"))
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
    except (KeyError, ValueError, RuntimeError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
