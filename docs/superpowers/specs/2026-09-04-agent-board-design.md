# agent-board — design

2026-09-04

A shared kanban board for coordinating several coding agents (multiple Claudes,
Codex, OpenCode) working in separate cmux terminals, with a human in the loop.

Replaces `.agent-bridge`, a copy-pasted two-agent file message bus that required the
human to keep telling each agent to check its inbox.

## Goals

- Any number of agents, addressed by **role**, not by vendor name.
- The human can see the board and add work without editing files by hand.
- Agents learn about work without polling and without re-reading the whole board.
- Coordination costs approximately zero tokens.
- One implementation, many projects.

## Non-goals

Deliberately excluded. Each was considered and rejected, not overlooked.

| Excluded | Why |
|---|---|
| Autonomous operation | Human stays in the loop. Removes leases, timeouts, budget caps, stop conditions. |
| Agent registry / roster | Roster state goes stale; a crashed agent stays registered forever. You never need to know who exists, only what is unclaimed. |
| A scheduler | Assignment is human, or an agent takes the top of its column. **Never use an LLM as a scheduler** — that is what makes agent boards expensive. |
| Leases / claim expiry | With a human watching, a stuck ticket is noticed. Add only if operation becomes unattended. |
| A database | Kills git as the audit trail. See "Why not SQLite". |
| Runtime schema validation | The predecessor's schemas were never enforced and nothing broke. |
| Configurable columns | Sounds free, is not. Fixed set for v1. |
| Drag and drop, auth, websockets | Scope creep. Buttons, localhost, meta-refresh. |

## Storage

Directories are columns. A ticket is one markdown file.

```
.agent-board/
  todo/     007-fix-login-redirect.md
  doing/    003-add-auth.md
  review/   005-refactor-api.md
  blocked/
  done/
```

**The directory is the only truth.** `status` is deliberately absent from
frontmatter, so there is nothing to drift and no reconcile step. Moving a column is
`mv` — atomic on POSIX, no locking.

### Ticket format

```markdown
---
id: "007"
title: Fix login redirect loop
owner: codex
created: 2026-09-04T13:53:00Z
branch: fix/login-redirect
---

Session cookie isn't cleared on 401, so the redirect bounces forever.
Repro: log in, revoke session server-side, refresh.

## comment — claude · 2026-09-04T14:10:00Z
Fixed in a1b2c3d. Root cause was middleware ordering.

## comment — codex · 2026-09-04T14:40:00Z
Reviewed. Logic is right, no regression test. Moving to blocked.
```

Frontmatter is a flat scalar map. `owner` and `branch` are optional.
Comments are appended sections, parsed on `^## comment — <by> · <iso8601>$`.

### Why markdown and not JSON

`comments[]` is the field several agents write concurrently, and it decides the
format.

- **Appending merges cleanly.** Two agents commenting simultaneously conflict in a
  JSON array every time; appended text merges without conflict.
- **Appending is cheaper.** Markdown appends. JSON requires parse, mutate,
  re-serialise, rewrite the whole file.
- **Humans read these.** The human is in the loop by design; JSON with `\n`-escaped
  prose is unpleasant in a terminal or a diff.
- **Agents write markdown more reliably.** A trailing comma blocks a JSON ticket.

Structure is not lost: **storage format and API format are separate decisions.**
Every read command takes `--json` and projects the structure a UI wants.

### Why not SQLite

A `.db` file is a binary blob: no diffs, no merges, no `git log` for a ticket. In a
system where several agents mutate shared state, that audit trail is worth more than
query power. Volume does not justify it either — a few hundred files is nothing for a
filesystem. If indexing is ever needed, SQLite returns as a **derived index rebuilt
from the files**, never as the source of truth.

## CLI

Single file, `board.py`, standard library only, **Python 3.11+**. The `board` shim
resolves a suitable interpreter explicitly (`python3.14`, `python3.13`, ... down to
plain `python3`, checked at `>= 3.11`) rather than trusting bare `python3` — on
macOS, the system `python3` is 3.9.6, too old for this codebase's syntax.

```
board init                              create column directories
board new "title" [--desc TEXT] [--column todo] [--owner NAME]
board list [column] [--json]
board show <id> [--json]
board take <id> --owner NAME            move to doing/, set owner
board move <id> <column>
board comment <id> "text" --by NAME
board watch <column> [--interval 5]
board serve [--port 8899]
```

Tickets are addressed by bare id (`board take 7` resolves `007`).

**ID allocation:** scan for the highest existing id across all columns, add one,
create with `O_EXCL`, retry up to five times on collision. Sufficient with a human in
the loop, and keeps ids short enough to say out loud.

**Writes** use temp-file-plus-atomic-rename. Ids are validated against `^[0-9]{1,6}$`
and column names against the fixed set before touching any path — ticket ids become
file paths, so path traversal is the obvious hole.

## Agent workflow

Agents are addressed by **role**, which is their cmux workspace name — `impl`,
`review`, `arch` — not by vendor. A Claude in the `review` workspace is `review`, and
swapping it for Codex later changes nothing.

An agent discovers its own role from `cmux identify --json` (which returns
`workspace_ref` but not the name) plus a lookup against `cmux workspace list`.

**Delivery is push, never poll.** `board watch review` polls directory mtimes locally
and prints one line per change; that pipes into `cmux send` to nudge the agent owning
that column. The agent then reads **only its own column**, never the whole board.

Nudges are **content-free** — "you have work, check your column". The payload stays
in the ticket, which is inspectable and version-controlled. This matters because
agents launched with `--dangerously-skip-permissions` or `--yolo` cannot distinguish
injected keystrokes from the human typing, so an instruction body must never travel
over `cmux send`.

## Web UI

`board serve` — stdlib `http.server`, bound to `127.0.0.1`, no auth, no npm, no
framework, no build step. Server-rendered HTML, POST/redirect/GET, 3-second
meta-refresh. Buttons to move a ticket between columns; a form to add one.

**The invariant: a hard refresh must reconstruct everything from disk.** The UI is a
projection and a command surface, never a state owner. No database, cache,
background reconciliation, scheduler, or hidden metadata. Every POST performs an
ordinary file operation — exactly what the CLI would do. Delete the UI and the board
is untouched.

Remote access, live collaboration, and auth are explicitly v2. Localhost is the MVP.

## Code, not the board

The board carries **coordination**: what to do, review requests, decisions, blockers.
**Git carries code.** Diffs never travel through tickets.

Multiple agents editing one working copy will corrupt each other. Each agent gets its
own **git worktree** — shared history, separate working directories, one cmux
workspace per worktree. Agents merge through git, which already solves concurrent
editing.

## Packaging

The repo holds `board.py`, a `board` shim, and this spec. A project holds only its
`.agent-board/` directory — state, not code.

Single dependency-free file, so copying, symlinking, or `curl`-ing all work. This is
a deliberate correction: the predecessor was a folder you copied, which is how it
ended up as N divergent copies across many repos. One file makes drift cheap to fix.

## Testing

- Frontmatter and comment parsing round-trips.
- `move` is atomic and leaves exactly one file.
- Concurrent `new` never produces duplicate ids.
- Concurrent `comment` on one ticket loses nothing.
- Path traversal via crafted id or column is rejected.
- The web UI reconstructs full state after a restart (the invariant, as a test).

## Open for v2

Remote/tailnet access with a shared secret. A cmux custom sidebar backed by the same
files. Leases, if operation ever becomes unattended. A derived SQLite index, if
volume ever justifies one.
