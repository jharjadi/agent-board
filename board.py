#!/usr/bin/env python3
"""agent-board - a file-backed kanban board for coordinating coding agents."""
import argparse
import errno
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


def sanitize_scalar(value: str) -> str:
    """Frontmatter values must be single-line. Collapse control chars to spaces."""
    cleaned = "".join(" " if (ch == "\n" or ch == "\r" or ord(ch) < 32) else ch for ch in value)
    return " ".join(cleaned.split()).strip()


def init_board(base: str | None = None) -> str:
    base = base or os.getcwd()
    root = os.path.join(base, BOARD_DIR)
    for col in COLUMNS:
        os.makedirs(os.path.join(root, col), exist_ok=True)
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


def create_ticket(root: str, title: str, description: str = "",
                  column: str = "todo", owner: str | None = None) -> str:
    column = validate_column(column)
    title = sanitize_scalar(title)
    owner = sanitize_scalar(owner) if owner else owner
    for _ in range(MAX_ID_ATTEMPTS):
        tid = next_id(root)
        # Reserve at the board ROOT, not inside a column: exclusivity must be
        # on the id alone, or two concurrent creates into different columns
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
            final_path = os.path.join(root, column, "%s-%s.md" % (tid, slugify(title)))
            os.makedirs(os.path.dirname(final_path), exist_ok=True)
            os.rename(reservation_path, final_path)
            return tid
        except BaseException:
            if os.path.exists(reservation_path):
                os.unlink(reservation_path)
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
    ticket.owner = sanitize_scalar(owner)
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

def _print_rows(rows) -> None:
    if not rows:
        print("(empty)")
        return
    for col, t in rows:
        owner = " @%s" % t.owner if t.owner else ""
        print("%s  %-8s %s%s" % (t.id, col, t.title, owner))


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
            comment_form = (
                '<form class="inline" method="post" action="/comment">'
                '<input type="hidden" name="id" value="%s">'
                '<input name="body" placeholder="Comment" required>'
                "<button>Add</button></form>" % esc(t.id)
            )
            cards.append(
                '<div class="t"><span class="id">%s</span> %s %s'
                '<div class="meta">%d comment(s)</div><div>%s</div><div>%s</div></div>'
                % (esc(t.id), esc(t.title), owner, len(t.comments), moves, comment_form)
            )
        cols_html.append(
            '<div class="col"><h2>%s (%d)</h2>%s</div>'
            % (esc(col), len(cards), "".join(cards) or '<div class="meta">empty</div>')
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        # A meta refresh reloads mid-typing and destroys whatever is in a form.
        # Poll instead, and skip the reload while the user is editing.
        "<script>setInterval(function(){"
        "var a=document.activeElement;"
        "if(a&&(a.tagName==='INPUT'||a.tagName==='TEXTAREA'))return;"
        "location.reload();},3000);</script>"
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
        if not args.no_agents:
            for path in write_agents_doc():
                print("wrote %s" % path)
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
    except (KeyError, ValueError, RuntimeError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
