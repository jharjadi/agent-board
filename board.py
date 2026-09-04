#!/usr/bin/env python3
"""agent-board - a file-backed kanban board for coordinating coding agents."""
import glob
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

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
    owner: str | None = None
    branch: str | None = None
    comments: list[Comment] = field(default_factory=list)


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
    current = None
    buf: list[str] = []
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


def init_board(base: str | None = None) -> str:
    base = base or os.getcwd()
    root = os.path.join(base, BOARD_DIR)
    for col in COLUMNS:
        os.makedirs(os.path.join(root, col), exist_ok=True)
    return root


def find_root(start: str | None = None) -> str:
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


def _all_ticket_paths(root: str) -> list[str]:
    paths = []
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


def create_ticket(root: str, title: str, description: str = "",
                  column: str = "todo", owner: str | None = None) -> str:
    column = validate_column(column)
    for _ in range(MAX_ID_ATTEMPTS):
        tid = next_id(root)
        id_path = os.path.join(root, column, "%s.md" % tid)
        try:
            fd = os.open(id_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            continue
        try:
            ticket = Ticket(id=tid, title=title, created=utc_now(),
                            description=description, owner=owner)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(render_ticket(ticket))
            final_path = os.path.join(root, column, "%s-%s.md" % (tid, slugify(title)))
            os.rename(id_path, final_path)
            return tid
        except BaseException:
            if os.path.exists(id_path):
                os.unlink(id_path)
            raise
    raise RuntimeError("could not allocate a ticket id after %d attempts" % MAX_ID_ATTEMPTS)


def load_ticket(path: str) -> Ticket:
    with open(path, encoding="utf-8") as fh:
        return parse_ticket(fh.read())


def find_ticket(root: str, tid: str) -> tuple[str, str]:
    tid = validate_id(tid)
    for col in COLUMNS:
        for pattern in ("%s-*.md" % tid, "%s.md" % tid):
            matches = sorted(glob.glob(os.path.join(root, col, pattern)))
            if matches:
                return col, matches[0]
    raise KeyError("no ticket with id %s" % tid)


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
    block = "\n## comment — %s · %s\n%s\n" % (by, utc_now(), body.strip())
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(block)


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
