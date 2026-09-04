# Decisions

Why agent-board is shaped the way it is. Recorded during the build so the
reasoning outlives the session that produced it. The spec states *what*; this
states *why*, including the things we tried and rejected.

## Rejected, with reasons

| Rejected | Why |
|---|---|
| Copying a folder into each project | The predecessor (`.agent-bridge`) was copied into many repos and diverged. One installed file, per-project state only. |
| An agent registry | Roster state goes stale — a crashed agent stays registered forever. You never need to know who exists, only what is unclaimed. |
| A scheduler | Assignment is the column, or a human. An LLM scheduler is the main reason multi-agent boards get expensive. |
| Leases / claim expiry | A human is in the loop and notices a stuck ticket. Revisit only if operation becomes unattended. |
| JSON tickets | `comments[]` is written concurrently; JSON arrays conflict in git every time and appends require a full rewrite. |
| SQLite | A binary blob kills `git log` for a ticket — the audit trail is worth more here than query power. |
| A `status` frontmatter field | The directory is the only truth, so nothing can drift and there is no reconcile step. |
| Presence / heartbeats | Stale presence is worse than none, because it is trusted. |

## Corrections made during the build

Three defects were in the plan itself, not the implementation. All were caught
by review after the tests were green:

1. **Id reservation was wrong twice.** `O_EXCL` on `NNN-slug.md` made exclusivity
   depend on the title; moving it to `<column>/NNN.md` still let two columns win
   the same id. It now reserves at the board root, then renames into the column.
2. **`add_comment` rewrote the whole file**, so concurrent comments were silently
   lost — contradicting the spec's own reason for choosing markdown. Now `O_APPEND`.
3. **Frontmatter injection.** A newline in a title terminated frontmatter early and
   bricked `board list` and every web page. Scalars are now sanitised on write.

## 2026-09-05 — concurrent mutation (found by Codex review)

`take_ticket` and `set_owner` were read-modify-write. When earlier review caught
`add_comment` doing the same thing, only that one function was fixed — the instance,
not the class. Two reproducible failures followed: an assignment landing beside a
comment **erased the comment**, and an assignment landing after a move **recreated the
ticket in its old column**, giving one id two homes.

The fix is one board-wide `fcntl.flock`. Three things a first attempt gets wrong:

- **`add_comment` must take the lock too.** `O_APPEND` makes the append atomic, but a
  writer that re-reads inside the lock can still be beaten by an append landing before
  its write. Re-reading only moves the race window. Every mutation cooperates or none
  of them are safe.
- **The lock file is permanent and never replaced.** Unlink or atomically swap it and
  waiters end up holding a different inode, so the lock silently stops working.
- **The lock does not prevent double claims.** It serialises two takes; both still
  succeed. That needs a separate guarded transition — `take --from todo` — inside the
  same critical section. Comparing owner strings instead would turn the advisory hint
  into authority and cross the design line.

The earlier justification for refusing claim arbitration ("a human will notice") was
weak: it assumes duplicate work becomes visible before it becomes expensive. A guarded
transition is transaction correctness, and needs no leases and no agent knowledge.

Worktrees were also contradictory — the README told you to give each agent one, while
`find_root` only walks parents, so each worktree found a different board.
`AGENT_BOARD_ROOT` now takes precedence and fails loudly rather than silently
discovering the wrong board.

## Rulings, verbatim

Extracted from the execution ledger.

**— worktree not used; feature branch `feat/board-mvp` instead — brand-new
**— cumulative test counts in the plan (5,13,18,24,29,33,38,42) were computed
- **Task 2** **— ID_RE `^[0-9]{1,6}$` accepts a trailing newline (Python `$` matches before \n).
- **Task 3** **— remove `threading.Lock`. Process-local, so it does nothing for the real
- **Task 3** **— the concurrency test must use real OS processes (subprocess), not threads.
- **Task 4** **— list_tickets sorts on the raw id STRING. Ids zfill to 3, so past 999 tickets
- **Task 6** **— the "first call with empty seen reports all existing tickets as changed" contract is
- **Task 6** **— watch_once sorts tid STRINGS lexicographically while list_tickets sorts int(id).
- **Task 8** **— Content-Length parse + body decode sit OUTSIDE do_POST's try, so `Content-Length: abc`
- **Task 8** **— do_POST catches (KeyError, ValueError) while main() catches those plus RuntimeError
- **Task 8** **— Content-Length is unbounded, so a huge value makes rfile.read() block the
- **Final** **— MY TASK 3 RULING WAS INCOMPLETE. I ruled "O_EXCL the id-only path" but the path is
- **Final** **— render_ticket interpolates title/owner into frontmatter unescaped, so a newline in a
- **Final** **— do_GET has no exception handling while main() and do_POST both catch four types.
- **Final** **— POST /comment is unreachable (no form targets it). The user explicitly asked during
- **Final** **— spec still says "Python 3.9+ (the macOS system python)" but the delivered code is
  root/{tid}.md — invisible to find/list, permanently inflates next_id. **— accept. The create
  "## comment — X · Y" line re-parses as an additional comment. **— accept for v1 and record
