# agent-board — threads and inbox

2026-09-05

Agents need to talk to each other about things that are not tickets, and the
conversation has to outlive the session. This adds two things to the board: a
**thread**, which is a conversation with no column, and an **inbox**, which is the
question "what is waiting on me", answered from the files.

Companion to `2026-09-04-agent-board-design.md`. Everything there still holds.

## Why

The board replaced `.agent-bridge`, and the bridge's wake-up machinery is gone for
good: with cmux, the agent that posts a message nudges the recipient itself. But the
bridge did one more thing the board does not. It kept the conversation. When this
board was built, Claude and Codex argued design, plan, and every task across many
rounds, and none of it survives, because there was no ticket to hang it on and no
tracker to file it in.

What the bridge's own history says a conversation is, measured across the 41 threads
in produxiom2:

- Almost all traffic is one loop: request, response, changes applied, approval, in
  rounds bound to a commit. Twelve message types and a dozen ad hoc ones reduce to
  two facts per message: **does this expect a reply**, and **which message does it
  answer**.
- The tool that answered "what needs me" guessed from the type of the newest message
  and was patched twice after it hid a real request. Both bugs disappear when the two
  facts above are explicit.
- Bodies are essays, median 3 KB, written as prose inside JSON string values. They
  are markdown.
- Every thread was gitignored. The history the human valued lived on one machine.

## Goals

- An agent can open a conversation with another agent without a ticket, in one
  command, and the other agent can find it without being told.
- A question stays visible until something answers it, whether or not the nudge
  landed.
- The human sees every conversation in the web UI and in `git log`.
- Tickets and threads carry the same message shape, so one inbox covers both.

## Non-goals

| Excluded | Why |
|---|---|
| Message types | Two facts, ask and re, encode the whole review loop. Types were guessed at and drifted; produxiom2 invented thirteen extra ones. |
| Thread status, close, archive | A thread is live while it holds an unanswered ask. A status field is what the board refused for tickets, for the same reason. |
| Inbox directories, unread counters, state files | The bridge had all three; its own "what needs me" tool ignored them and they split by alias once. The inbox is a query. |
| A roster or alias map | `to` is a free string, like `owner`. The bridge's claude/engineer split came from renaming mid-flight; the fix is choosing names once. |
| Notifications | The posting agent nudges the recipient over cmux. The board never does. |
| Verifying `commit` against git | It is text the board carries. The reviewer checks it. |
| Per-role memory files | Real need, wrong home. They are project knowledge, not messages. |
| Importing `.agent-bridge` JSON threads | Possible later as a one-off script. Not part of this. |

## Storage

```
.agent-board/
  todo/ doing/ review/ blocked/ done/     tickets, unchanged
  threads/  012-shelve-restore-design.md  conversations with no column
```

A thread is a markdown file with the same frontmatter a ticket has. It has no
description; its opening post is message 1. **A file is a thread because it is in
`threads/`**, nothing else.

```markdown
---
id: "012"
title: Shelve/restore design, before any spec
created: 2026-09-05T02:10:00Z
ticket: "009"
---

## comment — claude · 2026-09-05T02:10:00Z · to codex · ask · commit a1b2c3d
Design round for shelve/restore. Attack the optional filter parameter first.

Three decisions Jimmy already made ...

## comment — codex · 2026-09-05T02:48:00Z · to claude · re 1 · ask · commit a1b2c3d
Changes requested. Two blocking findings.

1. Shelving does not make the identity read-only ...

## comment — claude · 2026-09-05T03:30:00Z · to codex · re 2 · ask · commit b4c5d6e
Revision 2. All three accepted, none argued.
...

## comment — codex · 2026-09-05T04:02:00Z · to claude · re 3 · commit b4c5d6e
Approved at b4c5d6e.
```

`ticket` is optional and links a thread to the ticket it is about. It is the only
new frontmatter field.

### Message header

Tickets and threads share one message shape. The header is the existing comment
header with optional trailers after the timestamp:

```
## comment — <by> · <iso8601>[ · to <name>][ · ask][ · re <n>][ · commit <text>]
```

- `to <name>` — who this is addressed to. A free string.
- `ask` — this message expects a reply. **Requires `to`.**
- `re <n>` — this message answers message `n` of the same file. `n` is the 1-based
  position of the message in the file, which is how agents already refer to them.
- `commit <text>` — the commit this message is about. Carried, never checked.

Written in that order. Read in any order, and an unrecognised trailer is ignored so a
newer writer does not make a file unreadable. Every existing ticket parses unchanged,
because the old header is the new header with no trailers.

A reply may itself be an ask. That is the whole review vocabulary:

| Bridge type | Now |
|---|---|
| review_request | ask, with commit |
| review_response, changes requested | re + ask |
| approval | re |
| changes_applied | re + ask |
| question / answer | ask / re |
| blocked | ask, to the human |
| task_created | the thread, or the ticket |

`by`, `to`, and `commit` go through `sanitize_scalar`, and the separator `·` is
removed from them, so none of them can terminate the header early or start a new
trailer. Today `by` is written raw; this fixes that in passing.

A body line that begins `## comment — ` still parses as a new message. That is the
known spoofing limitation on ticket 001 of this repo's own board, unchanged here.

### Ids

Threads and tickets draw from one allocator. `next_id` scans `threads/` as well as
the columns, and creation reserves `<id>.md` at the board root with `O_EXCL` exactly
as tickets do, then renames into `threads/`. So `board show 12` opens whichever it is,
and "ticket 7 or thread 7" never has to be asked.

## The inbox rule

Stated once, applied everywhere:

> A message is **pending** for `name` when it carries `ask`, its `to` matches `name`
> case-insensitively, and no later message in the same file carries `re` pointing
> at it.

Any later `re` answers it: the recipient's reply, the asker's own "never mind", or a
newer ask that supersedes it. The rule reads the files and nothing else. A missed
cmux nudge costs nothing, because the ask is still there the next time anyone looks.

## CLI

```
board thread "title" "opening message" --by NAME [--to NAME] [--ask] [--commit TEXT] [--ticket ID]
board comment <id> "body" --by NAME [--to NAME] [--ask] [--re N] [--commit TEXT]
board threads [--json]
board inbox [NAME] [--json]
board show <id> [--last N] [--json]
board watch threads [--interval 5]
```

**`board thread`** creates the thread and writes message 1 in one step, then prints
the id. Two steps to start a conversation is the friction that stops it happening.

**`board comment`** works on a thread or a ticket. All four flags are optional.
`--ask` without `--to` is refused. `--re N` is refused unless message `N` exists in
that file, checked under the board lock before the append.

**`board threads`** lists threads by last activity, newest first: id, title, message
count, pending count, last message time and author.

**`board inbox codex`** lists pending asks for `codex`, newest first: id, whether it
is a ticket or a thread, message number, from, time, commit if any, and the first
line of the body. Agents therefore write a one-line summary first, which the bridge
already trained them to do. **`board inbox`** with no name lists every pending ask
with its recipient. That is the human's view.

**`board show 12 --last 3`** prints the frontmatter, the description if it is a
ticket, a one-line count of the messages skipped, and the last three. An agent
reading a 74-message thread cold must not load 220 KB to find the latest round.

**`board watch threads`** emits changed thread ids, for a human-side nudger that
wants to see conversations as well as columns. `threads` is accepted by `watch`
only; it is not a column.

`move`, `take`, and `assign` refuse a thread id with a plain message. A thread has
no column and no owner.

`--json` on `show`, `threads`, and `inbox` projects the new fields: `n`, `to`,
`ask`, `re`, `commit`.

## Web UI

A **threads panel** beside the columns. Each card: title, id, message count, a
badge with the number of asks pending for anyone when it is not zero, last activity. Expanding a card
shows the messages, rendered as ticket comments are now, with small badges for `to`,
`ask`, `re n`, and `commit`. Ticket cards render the same badges on their comments.

A **waiting-on** box. Typing a name reloads `/?inbox=<name>` and lists that name's
pending asks at the top of the page, each linking to its ticket or thread. The name
is in the URL, so the three-second refresh keeps it. Nothing is stored.

The comment form on tickets and threads gains `to`, `ask`, and `re`, so the human can
answer from the browser. A **new thread** form takes title, body, by, to, ask.

Every POST calls the same function the CLI calls. Hard refresh rebuilds all of it
from disk. The invariant from the original spec is unchanged.

## Agents block

`board init` writes an updated block into `AGENTS.md` and `CLAUDE.md`:

- Run `board inbox <you>` at the start of every session and after every nudge.
- To ask another agent something that is not a ticket, `board thread`. To ask about a
  ticket, `board comment` on it with `--to` and `--ask`.
- Answer with `--re <n>`. If your answer needs a reply, add `--ask`.
- Put a one-line summary on the first line of the body.
- After posting, nudge the recipient yourself if you know its pane. The board never
  does, and the nudge carries no content.

## Testing

- The old header parses to the same `Comment` as before. Each trailer round-trips
  alone and together. An unknown trailer is ignored. A `by` or `to` containing `·`
  or a newline is sanitised.
- The inbox rule: unanswered ask is pending; answered by `re` is not; a reply that
  asks again is pending for the other party; an ask on a ticket and an ask in a
  thread both appear; the asker's own `re` clears it; name matching ignores case.
- `--ask` without `--to` is refused. `--re` to a message that does not exist is
  refused.
- Concurrent `board thread` and `board new` from real processes never share an id.
  The existing subprocess harness is reused.
- `move`, `take`, and `assign` refuse a thread. `comment` accepts one.
- `board show --last` skips the right messages and says how many.
- `board watch threads` reports a thread that gained a message.
- The web UI renders the threads panel, badges, and the waiting-on list, and every
  new POST produces the same file the CLI would.
- The threads panel and forms are exercised in a browser before the change is called
  done. Two layout bugs shipped past a green suite before.

## Decisions to record

Appended to `docs/decisions.md`:

- Threads are column-less; there is no status and no close. Liveness is "has an
  unanswered ask".
- No message types. Two trailers, `ask` and `re`, carry the review loop.
- Pending is computed from `ask` and `re`, never from who spoke last, because
  produxiom2's tool was patched twice for exactly that guess.
- Names are free strings with no roster and no aliases.
- The board never notifies. The poster nudges.
- One file per thread, appended under the existing lock, not one file per message.
  Codex argued for per-message files on git-merge grounds; the board targets one
  machine with `AGENT_BOARD_ROOT`, and one file reads as one document.

## Open for later

An importer for `.agent-bridge` JSON threads. Hardening against a body line that
spoofs a header. `board show --since <n>` if `--last` proves the wrong shape.
