# agent-board

A file-backed kanban board for coordinating coding agents. Directories under
`.agent-board/` are columns, each ticket is a markdown file, git is the history.
Single file `board.py`, standard library only, **Python 3.11+**.

## Before proposing any feature

Read `docs/decisions.md`. It records what was deliberately rejected and why — a
registry, a scheduler, leases, JSON tickets, SQLite, a `status` field, presence
indicators. Several look like obvious improvements and are not; they are what turns
a board into an expensive orchestrator.

The line that matters: **the board never knows which agents exist.** Assignment is
the column. `board assign` is an advisory hint only.

## Working here

```bash
python3.14 -m unittest discover -s tests -v    # NOT python3 — that is 3.9.6 here
```

- Everything lives in `board.py`. Keep it one file — it is a packaging requirement,
  so the tool can be copied, symlinked, or curled on its own.
- Standard library only. No framework, no npm, no build step.
- The web UI is a projection: it owns no state, and a hard refresh must rebuild the
  page from disk. Every POST performs an ordinary file operation.
- Editing `board.py` needs a `board serve` restart; the CLI picks up changes at once.
- Exercise UI changes in a browser before calling them done. Two layout bugs shipped
  past a full green suite because no test renders a page.

## Design

`docs/superpowers/specs/2026-09-04-agent-board-design.md` — what and why.
`docs/superpowers/plans/2026-09-04-agent-board.md` — how it was built.
`docs/decisions.md` — every ruling made during the build.
