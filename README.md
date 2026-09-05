# agent-board

A file-backed kanban board for coordinating several coding agents with a human in
the loop. Directories are columns, tickets are markdown files, git is the history.

No dependencies. Python 3.11+.

![The board](docs/board-ui.png)

## Install

```bash
git clone https://github.com/jharjadi/agent-board.git
ln -s "$PWD/agent-board/board" ~/.local/bin/board   # or /usr/local/bin
```

Nothing is ever copied into your projects. Install once; each project gets only its
own `.agent-board/` state.

## Upgrade an existing project

1. Update the shared agent-board checkout on `main`:

   ```bash
   git -C /path/to/agent-board pull --ff-only
   ```

   Projects using the installed symlink get the new CLI immediately. There is
   nothing to reinstall or copy into each project.

2. Refresh each project's board and agent instructions:

   ```bash
   cd /path/to/my-project
   board init
   ```

   Running `init` again preserves existing tickets and comments, creates any
   missing directories such as `threads/`, and refreshes the board sections in
   `AGENTS.md` and `CLAUDE.md` while preserving other instructions. With shared
   worktrees, run this in the project directory containing the shared board.

3. Stop that project's running board server with **Ctrl+C**, then restart it
   from the project directory using its existing port:

   ```bash
   board serve --port 8934              # example; keep your project's port
   ```

   Refresh the browser to see Threads and the New thread form. Ask active agents
   to reread the updated instructions, or start a new agent session.

No ticket migration is needed. Existing comments remain readable; to create an
inbox request, post a message with `--to NAME --ask`, and answer it with `--re N`.
Old requests written only in prose do not automatically become inbox entries.
Importing historical `.agent-bridge` conversations is not implemented; keep those
archives. See [Talking to each other](#talking-to-each-other) for the new commands.

## Use

```bash
cd my-project
board init                              # creates .agent-board/
board new "Fix login redirect loop" --desc "401 doesn't clear the cookie"
board list
board take 1 --owner codex              # -> doing/
board take 1 --owner codex --from todo  # refuse if someone already claimed it
board comment 1 "Fixed in a1b2c3d" --by claude
board move 1 review
board assign 1 codex                    # advisory owner hint, no move
board thread "Design A vs B" "Attack the filter first" --by claude --to codex --ask
board inbox codex                       # requests waiting on codex
board comment 2 "Changes requested" --by codex --to claude --ask --re 1 --commit a1b2c3d
board threads                           # conversations, newest activity first
board show 2 --last 3                   # latest messages, keeping their numbers
board serve                             # http://127.0.0.1:8899
```

The web UI can create, move, and assign tickets, start threads, and post replies. The UI
is a projection over the filesystem: it owns no state, and a hard refresh rebuilds
the whole page from disk.

Editing `board.py` needs a `board serve` restart to take effect; the CLI picks up
changes immediately.

## Driving it with AI

`board init` appends a short block to the project's `AGENTS.md` and `CLAUDE.md`, so
agents discover the board on their own. That works the same whether the agent runs
in a terminal, a cmux pane, or a VS Code extension — they all read the project's
instruction files.

Which means you assign work in plain English:

> Take ticket 1 from the board. You're `codex`.

The agent runs `board show 1`, claims it with `board take 1 --owner codex`, does the
work, and reports with `board comment`. It checks `board inbox codex` for addressed
requests and answers to its own questions.

Hand it on the same way:

> Anything in review? You're `claude`.

That is the whole handoff. One agent moves a ticket to `review`, the next picks it
up from there — no relaying through you.

### Assignment is the column

Moving a ticket to `review` assigns it to whoever watches `review`. You never need
to know which agent that is today, or whether it is Claude or Codex or a person.

`board assign 7 codex` sets an **advisory** owner hint on top of that. The board
routes nothing and does not know which agents exist — it is a sticky note, and a
different agent picking the ticket up is fine.

This is deliberate. A board that knows about agents wants to know which are running,
which are free, and what to do when one dies. That is a scheduler, and a scheduler
made of LLM calls is what makes multi-agent setups expensive.

### Standing roles

Give an agent a column and leave it there. **Say it once, or the nudge alone is not
enough.** In a rehearsal, a Codex that had only been nudged with "Anything in review?"
found the ticket, read it, and answered the question literally: yes, there is one. It
reviewed nothing, because the agents block is role-neutral on purpose and nothing had
told it whose job the review column was. This text fixed it for the rest of the
session:

> You are the reviewer, standing role. When a ticket is in review: read it with
> `board show`, review the branch it names, run the tests, post your verdict with
> `board comment <id> "..." --by codex`, then `board move <id> done` if approved or
> `board move <id> blocked` with what must change. Never edit code yourself.

From then on "Anything in review?" is the whole handoff.

### The reviewer nudges back

The agent that posts is the one that knows something changed, so it does the
nudging. Tell it once where you are:

> After you have posted the verdict and moved the ticket, run `cmux send --workspace
> <ws> --surface <surface> "<project>: ticket N reviewed by codex"`, then `cmux
> send-key ... enter`. Do not put your answer in the nudge; the board carries it.

Nudge after both actions, or the other agent reads a verdict on a ticket still in
`review`. Name the project, because one engineer may be running two. In the
rehearsal Codex ran that from inside its sandbox and the nudge arrived as a user turn
in the other agent's session; no human relayed anything. `cmux identify --json` in a
pane tells you its refs.

### Launching an agent in a pane

```bash
cmux workspace create --name reviewer --cwd /abs/path/to/project --focus false \
  --command '/bin/zsh -lc "exec /opt/homebrew/bin/codex -a never -s workspace-write"'
```

Codex reads the project's `AGENTS.md` at start, so it finds the board on its own.
`-a never` never stops for approval; `-s workspace-write` lets it write inside its
working directory and nowhere else, so a board outside that directory needs to be
added to its writable roots. The login shell reads `.zprofile`, not `.zshrc`; if
`board` is only on your `PATH` through `.zshrc`, use `-lic`. More in
[`docs/migrating-from-agent-bridge.md`](docs/migrating-from-agent-bridge.md).

### Auto-nudge (optional)

So you never have to say "check the board":

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

### Talking to each other

Use a thread for a conversation that does not need a ticket. The command prints
its ID; use that ID when replying:

```bash
board thread "Float or Decimal?" "Should calc keep returning floats?" --by claude --to codex --ask
board inbox codex
board comment <id> "Keep float; changing return types would break callers." --by codex --to claude --re 1
board inbox claude
```

Threads are markdown files under `.agent-board/threads/`, with no column, owner,
status, or close action. Tickets and threads share IDs and the same message format:

- `--to NAME` addresses a message; `--ask` requests a reply and requires `--to`.
- `--re 1,2` answers earlier messages in that same file. **An ask stays pending
  until a later message explicitly lists it in `re`.**
- `--commit TEXT` records the commit being discussed without checking Git.

`board inbox NAME` shows requests awaiting your reply, then answers to your own
asks that you have not acknowledged. Posting anything in that file after an answer
acknowledges it; reading alone does not. A reply can request another reply with
`--re 1 --to NAME --ask`. An approval only needs `--re 1`.

`board inbox` without a name lists every pending ask, also shown in the web UI's
Waiting strip. `board threads --json`, `board inbox NAME --json`, and
`board show ID --last 3 --json` expose the same information to scripts. Message
numbers in a shortened view remain the original numbers in the file.

Use `--body-file PATH` for long messages, or `--body-file -` to read stdin. Each
message should start with a one-line summary for the inbox. The board does not
send notifications; a poster may nudge a recipient separately.

## Replacing `.agent-bridge`

This board replaces the `.agent-bridge` mailbox that preceded it. Every bridge concept
has a home here or a reason it does not, and the two role files and two memory files
the bridge grew are worth keeping in your project as they are. The steps, the mapping,
and the distilled role contracts are in
[`docs/migrating-from-agent-bridge.md`](docs/migrating-from-agent-bridge.md).

Conversations without tickets now have threads and an inbox. See
[`docs/superpowers/specs/2026-09-05-threads-and-inbox-design.md`](docs/superpowers/specs/2026-09-05-threads-and-inbox-design.md).

## Rules

- **Replies are explicit.** Use `--re`; who spoke last does not determine whether an ask is answered.

- The **directory is the only truth**. There is no `status` field.
- The board carries coordination; **git carries code**. Diffs never go in tickets.
- Give each agent its own **git worktree** so they cannot corrupt each other — and point them all at one board with `AGENT_BOARD_ROOT=/abs/path/to/.agent-board`. Without it each worktree discovers its own copy, and moving a ticket in one is invisible to the others.

## Known limitations

- **Message numbers are positions.** Appending preserves them, but inserting or deleting message headers by hand changes what later `re` references mean. Edit bodies, not the message sequence.

- **Watcher granularity.** `board watch` detects change by file mtime. Two edits landing within the same mtime tick are seen as one change, and because `os.replace` does not update mtime, a ticket that leaves a column and returns before the next poll is not re-flagged. Poll interval defaults to 5s.
- **Advisory locking only.** Mutations serialise on `fcntl.flock` over `.agent-board/.lock`, which the kernel releases when a process dies — no stale locks. But it is advisory: hand-editing a ticket file, or any tool that does not go through `board`, bypasses it entirely. POSIX only; `fcntl` does not exist on Windows.
- **Unguarded claims still race.** `board take 1 --owner x` will happily claim a ticket someone else already took. Pass `--from todo` for a guarded transition that refuses unless the ticket is still where you expect. There are deliberately no leases and no expiry — the guard is transaction correctness, not the board learning about agents.
- **flock over NFS is unreliable.** Keep the board on a local filesystem, not a NAS mount.
- **Local only.** `board serve` binds `127.0.0.1` and has no authentication. Do not expose it to a network or a tailnet as-is.
- **The board lives in the same repo as the code.** A ticket moved while you are on a feature branch is a change on that branch. Commit board state on the main branch, or give each agent a worktree and one shared board via `AGENT_BOARD_ROOT`.

## Why it is shaped this way

**Read [`docs/decisions.md`](docs/decisions.md) before proposing a feature.** It lists
what was deliberately rejected and why: an agent registry, a scheduler, leases, JSON
tickets, SQLite, a `status` field, presence indicators. Several look like obvious
improvements. They are what turns a task board into an expensive orchestrator — the
predecessor to this tool burned tokens on agents deciding who should do the work
rather than doing it.

The line that matters: **the board never knows which agents exist.**

Also there: the three defects that shipped past a green test suite and were caught by
review, including an id-allocation scheme that was wrong twice.

## Design

- [`docs/decisions.md`](docs/decisions.md) — what was rejected, and why
- [`docs/superpowers/specs/2026-09-04-agent-board-design.md`](docs/superpowers/specs/2026-09-04-agent-board-design.md) — the design
- [`docs/superpowers/plans/2026-09-04-agent-board.md`](docs/superpowers/plans/2026-09-04-agent-board.md) — how it was built
- [`docs/superpowers/specs/2026-09-05-threads-and-inbox-design.md`](docs/superpowers/specs/2026-09-05-threads-and-inbox-design.md) — threads and the inbox
- [`docs/migrating-from-agent-bridge.md`](docs/migrating-from-agent-bridge.md) — replacing `.agent-bridge`, and what it taught

## Scope

Built for one person's workflow and shared in case the reasoning is useful. Issues and
forks welcome; **feature PRs probably not** — the non-goals in `docs/decisions.md` are
the design, not a backlog. If you need it to work differently, forking a single
dependency-free file is genuinely the easier path.

MIT licensed.
