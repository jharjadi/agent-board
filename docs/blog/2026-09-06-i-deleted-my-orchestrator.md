---
title: "Your agent orchestrator is a black box. Mine is a folder."
published: false
description: "Coordinate two coding agents with directories and markdown files. No scheduler, no runtime registry, no tokens spent deciding who does the work."
tags: ai, productivity, opensource, tooling
cover_image: https://raw.githubusercontent.com/jharjadi/agent-board/main/docs/blog/images/01-board-top.png
---

There is a moment with every agent orchestration framework where you look at the
token bill, then at what actually got built, and cannot account for the gap.

You know the shape of it. A framework with tens of thousands of stars. A
supervisor agent that decides which worker agent should act. A planner that
re-plans. Some of your spend went to writing code, and some went to a model
thinking about which model should think about the code, and you cannot separate
the two because the interesting part happened inside a Python object that no
longer exists.

I got tired of that. Not of agents, which work fine. Of not being able to answer
three questions:

1. What is each agent doing right now?
2. Who is waiting on whom?
3. What did they decide, and why?

Every framework I tried answered those with a dashboard, a trace viewer, or
nothing. I wanted to answer them with `ls`.

## What you get

Here is the whole idea. Your agents coordinate through a directory:

```
.agent-board/
    todo/
        005-max-id-attempts-5-can-starve.md
    doing/
        001-comment-body-can-spoof-a-comment-header.md
    review/
        011-bare-cr-bypasses-body-escaping-and-forges-messages.md
    blocked/
    done/
    threads/
    agents          <- who works here, written by you
```

Columns are directories. Tickets are markdown files. An agent claims work by
moving a file. That is the entire data model, and you already know how to operate
it, because it is `ls`, `mv`, `cat`, `grep` and `git log`.

Which means:

- **Coordination costs zero tokens.** Nothing calls a model to decide who works
  next. There is no supervisor, because assignment is which folder the file is
  in. The only tokens you spend are the ones your agents spend doing the work.
- **You can read all of it.** Not a trace viewer. The actual conversation, in
  your editor, in a markdown file, right now.
- **You can edit it.** An agent misunderstood the ticket? Fix the sentence. No
  API, no migration, no restart. It is a text file.
- **Nothing runs unless you run it.** No daemon, no background scheduler, no
  process to babysit or pay for.
- **Your reviewer's argument is a diff.** When two agents disagree and one
  changes its mind, that is in `git log` a year later.

And you can read the implementation. It is one Python file, 1,585 lines,
standard library only. Not "small for a framework." Small enough that you could
sit down and understand every line this afternoon, and then change the parts you
disagree with.

If you have Python 3.11+ on macOS or Linux, you `curl` it into a project and it
works. No npm, no build step, no dependencies. It uses `fcntl` for locking, so
POSIX only.

![The board](https://raw.githubusercontent.com/jharjadi/agent-board/main/docs/blog/images/01-board-top.png)

There is a web UI, and it owns nothing. It is a projection of the directory.
Every button performs an ordinary file operation, and a hard refresh rebuilds the
page from disk. Kill the server mid-sentence and you lose nothing, because there
was nothing in it. Use it when you want to see the shape of things; use the CLI
for everything else.

## The whole protocol, in eight commands

Claude picks up a bug and hands it to Codex:

```bash
board init
board new "Divide crashes on zero divisor"
board take 1 --owner claude
board comment 1 "Fixed in a1b2c3d. Guard clause plus a test." \
    --by claude --to codex --ask --commit a1b2c3d
board move 1 review
```

Codex, in its own terminal, asks what is waiting on it:

```bash
$ board inbox codex

AWAITING YOUR REPLY (1)
  001  ticket #1   claude to codex  2026-09-06 09:12:04  a1b2c3d
        Fixed in a1b2c3d. Guard clause plus a test.
```

It reviews, answers, closes:

```bash
board comment 1 "Approved. Guard is correct and the test covers zero." \
    --by codex --to claude --re 1
board move 1 done
```

That is it. Two flags carry the entire protocol. `--ask` says a reply is
expected. `--re 1` says which message this answers. There is no message type, no
status field, no state machine. One rule decides everything:

> **An addressed message carrying `ask` is pending for a recipient until a later
> message by that recipient lists its number in `re`.**

Not "who spoke last." A plain message is never pending at all. Only an ask is.
Address two people and it stays pending for each of them separately, so you can
always see who has yet to reply; a `re` from the asker withdraws it for everyone.

Everything else in this post is that rule doing its job.

## The screen you will actually live in

![The waiting strip](https://raw.githubusercontent.com/jharjadi/agent-board/main/docs/blog/images/03-waiting-strip.png)

Every unanswered question on the board, across every ticket, in one strip. No
unread badges, no per-agent filter, no notification service. If a question is
open, it is there until something answers it. `board inbox` prints the same thing
in your terminal.

The first time you run it and it says `(nothing pending)`, that is not a guess
about the conversation. It is a fact about the files.

## What this buys you, concretely

Last week the board caught a bug in itself, and the way it happened is the best
argument I have for working this way.

I shipped a feature. 161 tests green. I asked Codex to review the diff, and it
filed this:

> [P2] Normalize carriage returns before neutralising message bodies. If a body
> contains a bare CR before a header-shaped example, this scan misses it because
> it splits only on newline. `load_ticket()` subsequently normalizes carriage
> returns into newlines, turning the example into a real message.

Messages are markdown, and a message body could contain something that looks like
a message header. If it did, it would parse as a real message, and a forged `re`
could mark a real question as answered. I had escaped that. Codex found the gap:
files are read in text mode, where Python turns a bare `\r` into a newline
*after* my escaping ran.

Two minutes to reproduce:

```
after real ask  -> messages=1  pending_asks=1
after CR body   -> messages=3  pending_asks=0
    #1 by='claude'  re=[]   ask=True   to='codex'
    #2 by='mallory' re=[]   ask=False  to=None
    #3 by='codex'   re=[1]  ask=False  to='claude'
```

Message #3 is attributed to an agent that never wrote it, and it silently cleared
a real request.

Here is the part that matters to you. That argument did not happen in a chat
window that I would close and lose. It happened on the board, so I still have it.
I pushed back on the severity, Codex moved it to P1, and then it caught me
overstating my own fix:

> The named test only exercises bare CR around a forged header. It does not
> itself cover CRLF, ordinary LF, or a trailing CR as claimed.

It was right. I had claimed four cases; the committed test had one. The green
suite did not catch that. My reviewer did, by reading what I wrote against what I
committed, and I can still pull up the exact exchange because it is a markdown
file in git.

That is the thing the dashboards never gave me. Not observability. Custody.

## What you are not paying for

The reason this stays small is that every feature I refused to build is written
down, with the reason. Condensed from `decisions.md`:

| Rejected | Why |
|---|---|
| An agent registry that agents write to | Roster state maintained at runtime goes stale. A crashed agent stays registered forever. (A list *you* write is fine — it never claims anyone is running.) |
| A scheduler | Assignment is the column, or a human. An LLM scheduler is the main reason multi-agent boards get expensive. |
| Leases / claim expiry | A human is in the loop and notices a stuck ticket. |
| JSON tickets | `comments[]` is written concurrently. JSON arrays conflict in git every time. |
| SQLite | A binary blob kills `git log` for a ticket. The audit trail is worth more than query power. |
| A `status` field | The directory is the only truth, so nothing can drift and there is no reconcile step. |
| Presence / heartbeats | Stale presence is worse than none, because it is trusted. |

These are not hypotheticals. This replaced a homegrown system of mine called
`.agent-bridge`, and before writing this I scanned `~/Source` rather than trust my
memory: on 6 September 2026 it was installed in **twelve** repositories holding
**176 conversations**, and every single copy had a `registry/` directory.

The registry went stale, because nothing removes a crashed agent from a list. The
status field drifted from the directory, because two places can disagree. The
JSON messages conflicted in git on every concurrent write. And because the whole
thing was copied per project, there were twelve versions of it, ten of which had
fallen behind the two I actually maintained.

The distinction I eventually landed on: a list **I** write is harmless, because it
only ever claims "this project has a reviewer". A list the **agents** write is the
one that rots, because it claims "the reviewer is available" and nothing corrects
it when that stops being true. The test I use now is: if every agent process dies
right now, is any file wrong?

Every one of those was a small, sensible yes at the time. That is how this
happens. Nobody sets out to build an orchestrator. You add a registry because you
need to know which agents exist, and eleven yeses later you are maintaining
infrastructure instead of shipping.

## Start here

```bash
curl -O https://raw.githubusercontent.com/jharjadi/agent-board/main/board.py
python3 board.py init
```

`init` writes a block into your `AGENTS.md` and `CLAUDE.md` telling your agents
the commands. They pick it up on their next run. Then give one agent a ticket and
tell the other to check `board inbox`.

Three things to hold onto, whether or not you use this:

1. **Assignment should be location.** A ticket in `review/` is in review. Two
   places that can disagree will eventually disagree.
2. **Have exactly one rule for "is this answered."** Not who spoke last, not a
   flag someone remembers to set.
3. **Keep it in plain text, in git.** The argument where your reviewer changed
   its mind is worth more in six months than the code it was about.

The tool is one Python file with no dependencies, no server to keep alive, and no
idea who you are. It is coordinating real work on a client project now, and the
most interesting thing it has done so far is help me find a bug in itself.

That is the bar I would hold any of these to. Not "can it orchestrate." Can it
get out of the way.

---

*[github.com/jharjadi/agent-board](https://github.com/jharjadi/agent-board), MIT.
Every command and transcript above is from the real board. The bug is ticket 011;
the fix is commit `98f93e0`. Codex reviewed this post before it went up, on the
board, and found eight things wrong with it.*
