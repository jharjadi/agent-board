# agent-board

A file-backed kanban board for coordinating several coding agents with a human in
the loop. Directories are columns, tickets are markdown files, git is the history.

No dependencies. Python 3.11+.

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

## Known limitations

- **Watcher granularity.** `board watch` detects change by file mtime. Two edits landing within the same mtime tick are seen as one change, and because `os.replace` does not update mtime, a ticket that leaves a column and returns before the next poll is not re-flagged. Poll interval defaults to 5s.
- **No claim arbitration.** `board take` does not lock a ticket. Two agents taking the same ticket at the same moment will both succeed and the last writer sets the owner. This is deliberate — a human is in the loop and will notice — so there are no leases and no expiry.
- **Local only.** `board serve` binds `127.0.0.1` and has no authentication. Do not expose it to a network or a tailnet as-is.

## Design

See `docs/superpowers/specs/2026-09-04-agent-board-design.md`.
