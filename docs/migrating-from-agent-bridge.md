# Replacing `.agent-bridge` with agent-board

For a project that has a `.agent-bridge/` directory. Eleven of them exist on one
machine, and no two are alike; that is the first reason to replace them. This page
says what maps to what, what to keep, what to leave behind, and the steps.

Written 2026-09-05 after a two-agent rehearsal on `mock-project/` in this repo, with
Claude as engineer and Codex as reviewer in a live cmux pane. Every claim about what
works below was observed there.

## What is here today, and what is specified but not built

Today the board has tickets, columns, comments, owner hints, `board watch`, and a
web UI. **Threads and an inbox are specified and planned, not shipped.** See
`superpowers/specs/2026-09-05-threads-and-inbox-design.md` and the plan next to it.
Until they land, a question to another agent that is not about a ticket has no
home, and "what is waiting on me" is `board list <column>`. The rehearsal hit
exactly that gap, which is why the spec exists.

## What maps to what

| `.agent-bridge` | agent-board |
|---|---|
| `registry/tasks/*.json` with a `status` field | A ticket file. The column is the status. |
| `assigned_to`, `reviewer` | `board assign` sets an advisory owner hint. The reviewer is whoever watches `review`. |
| `threads/<TASK>/NNNN-<type>.json`, one file per message | Comments appended to the ticket. Threads for topics without a ticket are planned. |
| Twelve message types plus ad hoc ones | Prose today. Planned: two trailers, `ask` and `re`, plus `commit`. |
| `head_commit`, `approved_head_commit` | Write the commit in the comment. Planned: a `commit` trailer. |
| `inbox/<agent>/`, `state/*.json`, unread counters | `board list review` for column-routed work. Planned: `board inbox <name>`, computed from `ask` and `re`, no state. |
| `actionable.py` | Same as above. Its two patches, where the type heuristic hid a real request, are why pending will be computed from explicit replies. |
| `bridge-watch.sh`, `wait-inbox.sh`, `bridge.py watch` | The poster nudges the recipient over cmux. Proven in rehearsal from inside Codex's sandbox. `board watch <column>` remains for a human-side nudger. |
| `session-banner.sh`, `stop_hook.sh` | A hook that runs `board list review` at session start or turn end. Recipe below. Becomes `board inbox <you>` when that lands. |
| `ENGINEER.md`, `REVIEWER.md` | Keep them, in the project, as role files. The board's agents block is role-neutral on purpose. Distilled versions below. |
| `memory/engineer.md`, `memory/reviewer.md` | Keep them, in the project. Not a board concern. Convention below. |
| `review.sh`, `review-status.sh`, `build.sh` | A live pane replaces all three. Recipes for the headless case below. |
| `jira.py` | Nothing. The board is the tracker. Keep the one asymmetry as a convention: the reviewer moves to `done`. |
| `prehandoff.py` | Keep and adapt. Run it before `board move <id> review`. |
| `codex-log.sh` | Codex tooling, not bridge tooling. Keep it wherever you keep Codex scripts. |
| `schemas/` | Nothing. No runtime validation, by decision. |
| `archive/YYYY-MM/` | Nothing. `done` is the archive and git is the history. |
| `locks/*.lock` with a ten-second timeout | `.agent-board/.lock`, a kernel-held flock released when the process dies. |
| Runtime gitignored, protocol committed | The board is committed. Only `.lock` is ignored. The history the bridge kept on one machine now travels with the repo. |

## Steps, per project

1. **Init.** In the project root, `board init`. It creates `.agent-board/` and appends
   the agents block to `AGENTS.md`, symlinking `CLAUDE.md` to it if absent.
2. **Cut the old instructions.** `grep -rn agent-bridge AGENTS.md CLAUDE.md docs/`
   and remove every reference. Replace the "read your inbox first" lines with the
   standing-role text from the README.
3. **Move the role files.** `.agent-bridge/ENGINEER.md` and `REVIEWER.md` become
   `docs/roles/engineer.md` and `docs/roles/reviewer.md`, rewritten in board
   vocabulary. The distilled contracts below are a starting point.
4. **Move the memory files.** `.agent-bridge/memory/*.md` to `docs/roles/memory/`.
   Keep their header rules; they are the valuable part.
5. **Save the old threads.** They are gitignored, so they exist only on this machine.
   `tar czf agent-bridge-threads-$(date +%F).tgz .agent-bridge/threads .agent-bridge/registry`
   and put the archive somewhere durable. An importer into board threads is listed as
   later work; do not delete the only copy.
6. **Open tickets for in-flight work.** One `board new` per active bridge task, with
   the latest thread summary as the description and the branch and head commit named.
7. **Delete the rest.** `.agent-bridge/tools`, `schemas`, `inbox`, `state`, `locks`,
   `tmp`, and the README and AGENTS.md inside it. Keep `prehandoff.py` if you use it.
8. **Commit `.agent-board/`.**
9. **Worktrees.** If each agent has its own worktree, export
   `AGENT_BOARD_ROOT=/abs/path/to/.agent-board` in every agent's shell so they share
   one board. Without it, each worktree discovers its own copy.
10. **Launch the agents.** Recipe below.

## Role files, distilled

The bridge's two role files are long because they record every lesson from a year
of rounds. Most of it survives a change of tool. Rewritten for the board:

### Reviewer

- Standing role: watch `review`. When a ticket appears, `board show <id>`, review the
  branch and commit named in the latest comment, run the suite against that commit,
  post the verdict with `board comment <id> "..." --by <you>`, then `board move <id>
  done` or `board move <id> blocked` with what must change. Never edit code.
- Review the commit you were asked about, and say which one you reviewed. If the
  head moved, say the review is stale.
- Verify, do not accept. Re-run a cited mutation. In the rehearsal Codex snapshotted
  the branch with `git archive` into a temp directory and ran the tests there, so the
  shared working tree was never touched. That is the right instinct.
- Name the mechanism, not just the defect. "Unreachable because X already refused
  that state" survives a refactor; "unreachable" does not.
- If your sandbox stops you verifying what you were asked to verify, say so and move
  the ticket to `blocked`. Do not approve on inspection alone.
- Contradict freely, retract freely. A retraction is more useful than consistency.
- You are the only one who moves a ticket to `done`. An agent that can mark its own
  work finished has no gate.

### Engineer

- Take work with `board take <id> --owner <you> --from todo`, so a ticket someone
  else already claimed is refused rather than stolen.
- Send each artifact as it is produced: design before spec, spec before plan, each
  task as it lands, the whole branch at the end. One review round per artifact,
  scoped, saying what not to review.
- Every handoff comment names the branch, the head commit, what changed, what you
  ran and what it said, and the one claim you are least sure of.
- Pair every review round with a generative ask: what would you cut, is the frame
  right, find a mutation my tests do not catch.
- Verify every finding in the code before acting on it. Push back with reasoning
  when one is wrong. Never let a reviewer suggestion silently reverse a ruling the
  human made.
- Run your project's pre-handoff gate before `board move <id> review`. The bridge's
  `prehandoff.py` caught: a cited test that does not exist, a test absent from the
  Makefile's run list, a cited commit that does not resolve, and the other role's
  memory file swept into the diff. Two tickets there took five rounds each and the
  code was right at round one; every rejection was checkable without a reviewer.
- Write the summary sentence last, from the output in front of you.

## Memory files, convention

Two files, one per role, append-only, in the project and committed. They exist for
different reasons and hold different things:

| | `reviewer.md` | `engineer.md` |
|---|---|---|
| Loses memory | Totally, every cold session | Gradually, at compaction |
| Holds | Mechanisms verified with a command | Decisions, their rationale, traps, and rulings the human made |
| Written by | The reviewer only | The engineer only |

Neither role writes the other's file. Seeding the reviewer's memory with the
engineer's beliefs turns an independent gate into an echo. Nothing unverified goes
in either: the next session trusts what it reads. Nothing the repo already records
goes in: if `rg` finds it in ten seconds, it is a lookup, not memory. No status: the
board is the durable record of what is in flight.

Entry format:

```
### <short claim or decision>
Established: <the command, review round, or ruling that established it, and when>
Matters because: <what goes wrong if a later session assumes otherwise>
```

## Recipes

### Launch a Codex reviewer in its own cmux workspace

```bash
cmux workspace create --name reviewer --cwd /abs/path/to/project --focus false \
  --command '/bin/zsh -lc "exec /opt/homebrew/bin/codex -a never -s workspace-write"'
```

`-a never -s workspace-write` is what `--full-auto` used to mean; the flag itself
does not exist in Codex 0.153. The login shell is what gives Codex a `PATH` with
`board` on it. Codex asks two startup questions, trust this directory and trust
hooks, that need keypresses. Then give it the standing role once, in plain English,
and nudge it with "Anything in review?" from then on.

Injecting the launch command into a pane by keystroke was blocked by Claude Code's
own permission classifier during the rehearsal. Creating the workspace with the
command is the clean path.

### The reviewer nudges back

Tell the reviewer once:

> After you post, run `cmux send --workspace <ws> --surface <surface> "board: ticket
> N has a reply from codex"` and then `cmux send-key --workspace <ws> --surface
> <surface> enter`. Do not put your answer in the nudge; the board carries it.

Codex ran both commands from inside its `workspace-write` sandbox and the nudge
arrived as a user turn in Claude's session. The human relayed nothing. Find the
target with `cmux identify --json` in the engineer's pane.

### Hook the inbox into session start or turn end

The bridge grew two of these because agents forgot to look. `stop_hook.sh` compared
the inbox with the last snapshot at the end of every Claude turn and emitted
`{"systemMessage": "..."}` when something new had arrived from someone else.
`session-banner.sh` printed waiting work at Codex start.

Until `board inbox` lands, the equivalent is a Stop or SessionStart hook that runs
`board list review` and emits a system message when the column is not empty. Once
the inbox exists it is `board inbox <you> --json`, with the comparison free, because
pending survives across turns by construction.

### Headless reviewer, when there is no pane

```bash
before=$(find .agent-board -name '*.md' -newer .agent-board/.lock | wc -l)
codex exec -C /abs/path/to/project -s workspace-write \
  "Do not upgrade any tooling and do not ask questions; this is non-interactive.
   You are the reviewer. Run board list review, review each ticket there per
   docs/roles/reviewer.md, post your verdict with board comment --by codex, and
   move it to done or blocked."
after=$(find .agent-board -name '*.md' -newer .agent-board/.lock | wc -l)
[ "$after" -gt "$before" ] || echo "codex exited without touching the board"
```

Two lessons from `review.sh`: **exit code 0 is not a review**, so compare the board
before and after; and **say what the round is and forbid detours**, because a bare
"go" once let a tooling version check ask a question, the non-interactive session
exited 0, and nothing was reviewed. Use `-s danger-full-access` only when the review
must run Docker or a database, and only then.

### Worktrees

`build.sh` handed a ticket to Codex in its own worktree branched from `origin/main`,
because two agents editing one working copy produce a file with half of each change
and no record of the loss. The rehearsal ran both agents in one checkout and it
worked only because they never edited at the same time, and because board moves made
on a feature branch had to be committed on `main` separately. Give each agent a
worktree, point them all at one board with `AGENT_BOARD_ROOT`, and remember what the
worktree does not isolate: a shared database. `build.sh` learned that the hard way
when one agent's migration appeared in the other's schema test.

## Left behind, on purpose

| Not brought forward | Why |
|---|---|
| Registry, per-agent inbox directories, state files, unread counters | The bridge's own "what needs me" tool ignored all of them, and they split by alias once. State that nothing reads is state that lies. |
| Message types | Twelve plus thirteen ad hoc ones reduced to two facts: does this expect a reply, and which messages does it answer. |
| `SUMMARY.json` | It copied task metadata and the last five summaries. It never preserved an argument. |
| Monthly archive moves | `done` is a column. Moving files out of the hot path solved a problem the board does not have. |
| Schema validation | Never enforced in the bridge and nothing broke. |
| Jira synchronisation inside the coordination tool | Two records of one fact drift. The board is the record. |
| Role names as aliases (`claude` for `engineer`) | Choose a name once and use it. The alias map existed because the rename happened mid-flight. |

## After threads land

Candidates from the bridge that become small once `board inbox` exists, recorded on
this repo's own board as ticket 010:

- `board inbox <you> --wait`: block until the pending set for a name changes, print
  it, exit. `wait-inbox.sh` and `bridge-watch.sh` both did this, and both learned the
  same lesson: a reply that lands while nobody is watching must still be reported
  the next time anyone looks. The pending rule gives that for free.
- The Stop-hook recipe above, as a documented hook config rather than a script.
- An importer for archived `.agent-bridge` thread JSON into board threads, for the
  two projects with history worth keeping.
