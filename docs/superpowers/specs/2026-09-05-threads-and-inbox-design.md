# agent-board — threads and inbox

2026-09-05

Agents need to talk to each other about things that are not tickets, and the
conversation has to outlive the session. This adds two things to the board: a
**thread**, which is a conversation with no column, and an **inbox**, which is the
question "what is waiting on me", answered from the files.

Companion to `2026-09-04-agent-board-design.md`. Everything there still holds.

Reviewed by Codex on 2026-09-05; its findings are folded in and marked where they
changed the design.

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
  two facts per message: **does this expect a reply**, and **which messages does it
  answer**.
- The tool that answered "what needs me" guessed from the type of the newest message
  and was patched twice after it hid a real request. Both bugs disappear when the two
  facts above are explicit.
- Requests are not always answered one at a time. Message 38 of one thread
  superseded three open requests at once, and message 39 answered 38 alone. A reply
  therefore names a list of messages, not one.
- Bodies are essays, median 3 KB, written as prose inside JSON string values. They
  are markdown, and they are too long to pass as shell arguments.
- Every thread was gitignored. The history the human valued lived on one machine.

## Goals

- An agent can open a conversation with another agent without a ticket, in one
  command, and the other agent can find it without being told.
- A question stays visible until something answers it, whether or not the nudge
  landed.
- The human sees every conversation in the web UI, and the files sit unignored in
  `.agent-board/` so committing the board commits them. The board never runs git.
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
| `board watch threads` | Cut on review. It would only re-test the mtime mechanism on another directory, and the poster nudges anyway. |
| A per-name filter in the UI | Cut on review. The page suppresses refresh while any input holds text, and POSTs redirect to `/`, so a filter box fights both. `board inbox <name>` is the per-name view. |

## Storage

```
.agent-board/
  todo/ doing/ review/ blocked/ done/     tickets, unchanged
  threads/  012-shelve-restore-design.md  conversations with no column
```

A thread is a markdown file with the same frontmatter a ticket has. It has no
description; its opening post is message 1. **A file is a thread because it is in
`threads/`**, nothing else. `board init` creates `threads/` and is idempotent; every
reader tolerates a board that has no `threads/` yet.

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
new frontmatter field. Codex would cut it; it stays because the human named
conversations about tickets as a real case, and it is one field shown as text on
the card.

### Message header

Tickets and threads share one message shape. The header is the existing comment
header with optional trailers after the timestamp:

```
## comment — <by> · <iso8601>[ · to <name>][ · ask][ · re <n>[,<n>...]][ · commit <text>]
```

- `to <name>` — who this is addressed to. A free string.
- `ask` — this message expects a reply. **Requires `to`.**
- `re <n>[,<n>...]` — this message answers the listed messages of the same file.
  `n` is the 1-based position of a message in the file, which is how agents already
  refer to them. A list, because supersession of several open requests at once is
  in the record.
- `commit <text>` — the commit this message is about. Carried, never checked.

**Parsing anchors on the timestamp**, not on the first separator. The current
`COMMENT_RE` takes any non-blank token as the timestamp, so it reads an author of
`alice · 2026-09-05T00:00:00Z · to bob` and a timestamp of `ask`. The new pattern
requires the timestamp to look like one: `\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ`. With that
anchor an old header, a new header, and an old header whose author happens to
contain `·` all parse the same way, because the author is everything before the
timestamp. The compatibility promise is exact: **any existing file whose authors do
not contain the literal text ` · to `, ` · ask`, ` · re `, or ` · commit ` parses to
the same messages as before.** Authors are short names typed at `--by`, so in
practice that is every file.

Trailers are written in the order above and read in any order. The rules on read:

- An unrecognised trailer is ignored, so a newer writer cannot make a file
  unreadable.
- A recognised trailer that is malformed, such as `re` with a non-integer, counts as
  absent. It never counts as something else.
- A duplicated trailer keeps its first occurrence.
- A `re` naming a message that does not exist, or a message at or after itself, is
  kept in the parsed message and has no effect on pending.

The rules on write, all refused with a plain message: `ask` without `to`; a `re`
entry that is zero, negative, not yet in the file, or the message being written; a
`by`, `to`, or `commit` that is empty after sanitising.

`by`, `to`, and `commit` go through `sanitize_scalar` and lose the separator `·`, so
none of them can end the header early or start a trailer. Today `by` is written
raw; this fixes that in passing.

**Body lines that look like a header are neutralised on write.** Any body line
matching the header pattern gets one leading space, which markdown still renders
and the anchored parser no longer matches. Until now a spoofed header only
mis-rendered a comment; with `re` it could clear a real request, so this is in
scope. Existing files are not rewritten; ticket 001 on this repo's board tracks the
residue.

A reply may itself be an ask. That is the whole review vocabulary:

| Bridge type | Now |
|---|---|
| review_request | ask, with commit |
| review_response, changes requested | re + ask |
| approval | re |
| changes_applied | re + ask |
| question / answer | ask / re |
| blocked | ask, to the human |
| batch supersession | ask, re listing the superseded messages |
| task_created | the thread, or the ticket |

### Ids

Threads and tickets draw from one allocator. `next_id` scans `threads/` as well as
the columns, and creation reserves `<id>.md` at the board root with `O_EXCL` as
tickets do, then renames into `threads/`. So `board show 12` opens whichever it is.

**The allocator has a race today, and it is fixed first.** `create_ticket` computes
the next id outside the board lock. Two creators can both compute 12; the first
reserves and renames away; the second then reserves the now-absent root `012.md` and
also wins 12. Codex reproduced the interleaving. The fix is to hold the board lock
across scan, reservation, and rename, for tickets and threads alike. `find_ticket`
then also refuses an id that matches more than one file instead of returning the
first.

`move`, `take`, and `assign` refuse a thread id. The refusal lives in the shared
functions that mutate a file, before any mutation, not only in CLI dispatch, because
`take` rewrites the file before it moves it.

### Readers and writers

Writers already serialise on the board lock. Readers do not take it, and a
multi-kilobyte append is not one atomic write, so a reader could see a `re` header
before the body behind it exists. Two rules close that:

- A message is appended with one `os.write` loop on an `O_APPEND` descriptor, under
  the lock, so the header and body land together as far as any cooperating reader
  can see.
- Every read that computes pending, which is `inbox`, `show`, `threads`, and the
  page render, takes the board lock for the duration of its snapshot. The files are
  small and the lock is brief.

## The inbox rule

Stated once, applied everywhere:

> A message is **pending** for `name` when it carries `ask`, its `to` matches `name`
> case-insensitively, and no later message in the same file lists it in `re`.

Any later `re` answers it: the recipient's reply, the asker's own "never mind", or a
newer ask that supersedes it and others with it. The rule reads the files and
nothing else. A missed cmux nudge costs nothing, because the ask is still there the
next time anyone looks.

**Two questions, not one.** Pending asks answer "what do I owe". They do not answer
"what came back to me": an approval is a reply with no `ask`, so the engineer who
asked for review would see an empty inbox and could not tell acceptance from
silence. Codex found this reviewing the migration guide. So the inbox has a second
section, computed the same way:

> A message is **answered and unseen** by `name` when `name` posted it with `ask`, a
> later message lists it in `re`, and `name` has posted nothing in that file after
> that answer.

Posting anything after the answer, a "thanks" or the next question, is the
acknowledgement, and it lives in the file like everything else. `board inbox <name>`
prints both sections: **awaiting your reply**, then **answered, not yet seen by you**.
With no name it prints every pending ask and nothing from the second section, since
"seen" is per name.

## CLI

```
board thread "title" [BODY] --by NAME [--to NAME] [--ask] [--commit TEXT] [--ticket ID] [--body-file PATH]
board comment <id> [BODY] --by NAME [--to NAME] [--ask] [--re N[,N...]] [--commit TEXT] [--body-file PATH]
board threads [--json]
board inbox [NAME] [--json]
board show <id> [--last N] [--json]
```

**`board thread`** creates the thread and writes message 1 in one step, then prints
the id. Two steps to start a conversation is the friction that stops it happening.

**`board comment`** works on a thread or a ticket. All flags are optional. The body
comes from the argument or from `--body-file PATH`, where `-` reads stdin. Bodies in
the record are 3 KB essays; shell quoting is not the place for them.

**`board threads`** lists threads by last activity, newest first: id, title, message
count, number of asks pending for anyone, last message time and author.

**`board inbox codex`** lists pending asks for `codex`, newest first: id, whether it
is a ticket or a thread, message number, from, time, commit if any, and the first
line of the body. Agents therefore write a one-line summary first, which the bridge
already trained them to do. **`board inbox`** with no name lists every pending ask
with its recipient. That is the human's view.

**`board show 12 --last 3`** prints the frontmatter, the description if it is a
ticket, a one-line count of the messages skipped, and the last three **with their
original numbers**, so `re` still means what it says.

`--json` on `show`, `threads`, and `inbox` projects the new fields: `n`, `to`,
`ask`, `re` as a list, `commit`.

## Web UI

A **threads panel** beside the columns. Each card: title, id, message count, a
badge with the number of asks pending for anyone when it is not zero, last
activity, and the linked ticket if any. Expanding a card shows the messages,
rendered as ticket comments are now, with small badges for `to`, `ask`, `re n`, and
`commit`. Ticket cards render the same badges on their comments.

A **waiting strip** at the top of the page lists every pending ask, newest first:
to, from, ticket or thread, first line, each linking to its card. No input, no
filter, no state. It is `board inbox` with no name, rendered.

The comment form on tickets and threads gains `to`, `ask`, and `re`, so the human can
answer from the browser. A **new thread** form takes title, body, by, to, ask.

Every POST calls the same function the CLI calls. Hard refresh rebuilds all of it
from disk. The invariant from the original spec is unchanged. Upgrading `board.py`
still needs a `board serve` restart; an old server shows new headers wrongly until
then, but rewrites them byte-for-byte, verified.

## Agents block

`board init` writes an updated block into `AGENTS.md` and `CLAUDE.md`:

- Run `board inbox <you>` at the start of every session and after every nudge.
- To ask another agent something that is not a ticket, `board thread`. To ask about a
  ticket, `board comment` on it with `--to` and `--ask`.
- Answer with `--re <n>`, listing every message you are answering. If your answer
  needs a reply, add `--ask`.
- Put a one-line summary on the first line of the body. Use `--body-file` for
  anything longer than a paragraph.
- After posting, nudge the recipient yourself if you know its pane. The board never
  does, and the nudge carries no content.

## Build order

1. Allocator lock and `find_ticket` ambiguity. Pre-existing bug, smallest change,
   everything else allocates through it.
2. Header parse and render with trailers, sanitising, spoof neutralising, and the
   round trip through `assign` and `take`, which rewrite whole files.
3. Threads: creation, lookup, refusal in mutators, `threads` listing.
4. Pending rule and `inbox`, `show --last`, body from file.
5. Web UI: panel, badges, strip, forms. Browser check.
6. Agents block, README, decisions.

## Testing

- The old header parses to the same `Comment` as before, including an old author
  containing `·`. Each trailer round-trips alone and together. A `re` list
  round-trips. An unknown trailer is ignored; a malformed `re` counts as absent; a
  duplicated trailer keeps the first.
- A body line shaped like a header is neutralised on write and does not become a
  message or clear an ask.
- **Trailers survive `assign` and `take`.** Append asks and replies to a ticket,
  assign it, take it, and check every header field, number, and pending result is
  unchanged. Both operations parse and rewrite the whole document; a parser-only
  round trip would miss a renderer that drops a trailer.
- The inbox rule: unanswered ask is pending; answered by `re` is not; a reply that
  asks again is pending for the other party; a `re` list clears several at once; an
  ask on a ticket and an ask in a thread both appear; the asker's own `re` clears
  it; name matching ignores case; a dangling or forward `re` changes nothing.
- Write-side refusals: `ask` without `to`; `re` zero, negative, forward, or self;
  empty name after sanitising.
- Concurrent creation from real processes, tickets and threads mixed, never shares
  an id. The existing subprocess harness is reused, and the test is written to
  exercise the scan-then-reserve window the old code left open.
- `find_ticket` refuses an ambiguous id.
- `move`, `take`, and `assign` refuse a thread inside the shared functions.
  `comment` accepts one.
- `board show --last` skips the right messages, says how many, and keeps numbers.
- A board with no `threads/` directory lists, shows, and serves without error.
- The web UI renders the panel, badges, strip, and forms, and every new POST
  produces the same file the CLI would.
- The panel and forms are exercised in a browser before the change is called done.
  Two layout bugs shipped past a green suite before.

## Decisions to record

Appended to `docs/decisions.md`:

- Threads are column-less; there is no status and no close. Liveness is "has an
  unanswered ask".
- No message types. Two trailers, `ask` and `re`, carry the review loop, and `re`
  is a list because batch supersession is in the record.
- Pending is computed from `ask` and `re`, never from who spoke last, because
  produxiom2's tool was patched twice for exactly that guess.
- Names are free strings with no roster and no aliases.
- The board never notifies and never runs git. The poster nudges; the human commits.
- One file per thread, appended under the existing lock, not one file per message.
  Codex first argued for per-message files and withdrew it for a one-machine board;
  what it asked for instead, and got, is a reader lock and a single append write.
- The header parser anchors on the timestamp. Anchoring on the first separator was
  the ambiguity Codex found.
- The allocator scan runs under the board lock. It did not, and two creators could
  share an id.

## Open for later

An importer for `.agent-bridge` JSON threads. Stable message ids instead of
positions, if hand edits to committed threads ever turn out to be common.
