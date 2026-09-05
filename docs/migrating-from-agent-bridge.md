# Replacing `.agent-bridge` with agent-board

For a project that has a `.agent-bridge/` directory. Eleven of them exist on one
machine, and no two are alike; that is the first reason to replace them. This page
says what maps to what, what to keep, what to leave behind, and the steps.

Written 2026-09-05 after a two-agent rehearsal on `mock-project/` in this repo, with
Claude as engineer and Codex as reviewer in a live cmux pane, then reviewed by a
second Codex against the record. Claims about what works are marked by how they are
supported: **in the files** means the committed board and git history show it;
**reported** means the engineer read it in the reviewer's pane transcript, which the
files do not contain.

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
| `in_reply_to` | Say "re your comment 3" in prose today. Planned: the `re` trailer, a list. |
| `head_commit`, `approved_head_commit` | Write the commit in the comment. Planned: a `commit` trailer. |
| `inbox/<agent>/`, `state/*.json`, unread counters | `board list review` for the reviewer, `board list blocked` for the engineer. Planned: `board inbox <name>`, computed from `ask` and `re`, no state. |
| `actionable.py` | Same as above. Its two patches, where the type heuristic hid a real request, are why pending will be computed from explicit replies. |
| `changes_requested`, then the fix round | The reviewer moves the ticket to `blocked` with what must change. `blocked` is the engineer's inbox for rejected work; the fix goes back through `review`. |
| `blocked` addressed to the human | A ticket for the human, `board new ... --owner <human>`. Planned: an `ask` addressed to the human, which the waiting strip shows. |
| `bridge-watch.sh`, `wait-inbox.sh`, `bridge.py watch` | The poster nudges the recipient over cmux (reported working from inside Codex's sandbox). `board watch <column>` remains for a human-side nudger. |
| `session-banner.sh`, `stop_hook.sh` | Deferred until `board inbox` exists. Until then a turn-end hook running `board list review` announces the reviewer's work to the engineer on every turn and misses the engineer's own rejected work. |
| `ENGINEER.md`, `REVIEWER.md` | Keep them, in the project, as role files, and **name them in the standing-role message** so they are read. Distilled versions below. |
| `memory/engineer.md`, `memory/reviewer.md` | Keep them, in the project. Not a board concern. Convention and entry points below. |
| `review.sh`, `review-status.sh` | A live pane: you can see the reviewer working, and it nudges back when done. Headless recipe below for when there is no pane. |
| `build.sh` | The worktree recipe below. Its steps survive: branch from `origin/main`, work package as a file, one agent per checkout. |
| `jira.py` | **Depends on the project.** On a client project with a Jira board, Jira stays the tracker of record and the board is coordination between agents, as the bridge was. Keep the one asymmetry either way: the reviewer, not the engineer, marks work accepted. On a project with no tracker, the board is the tracker. |
| `prehandoff.py` | Keep and adapt. Run it before `board move <id> review`. |
| `codex-log.sh` | Codex tooling, not bridge tooling. Move it out of `.agent-bridge/tools` to wherever Codex scripts live before deleting the directory. |
| `schemas/*.json` | Nothing. The board validates ids, columns, and frontmatter in code and has no message schema; the bridge's schemas were never enforced. |
| `archive/YYYY-MM/` | Nothing. `done` is the archive and git is the history. Until threads exist, a design conversation with no ticket has no home; hold those in the backup. |
| `locks/*.lock` with a ten-second timeout | `.agent-board/.lock`, a kernel-held flock released when the process dies. |
| Runtime gitignored, protocol committed | The board is committed. Only `.lock` is ignored. Every committed state travels with the repo; intermediate states between commits do not. |

## Steps, per project

0. **Pilot on one project first.** Run a full cycle there, including a rejection and a
   resubmission, before touching the other ten. Two approvals on a toy project do not
   exercise the cutover.
1. **Inventory the callers.** `grep -rn agent-bridge . --exclude-dir=.git
   --exclude-dir=node_modules`, and read the hits in `Makefile`, `.githooks/`,
   `.github/`, CI config, and any script directory. Old automation keeps calling
   deleted paths and fails quietly.
2. **Back up the whole bridge, then verify the archive.**
   `tar czf agent-bridge-$(date +%F).tgz .agent-bridge && tar tzf agent-bridge-*.tgz | wc -l`.
   Threads, registry, and `archive/` are all gitignored; this is the only copy. Put
   it somewhere durable. Do not delete anything until the archive lists what you
   expect.
3. **Init.** In the project root, `board init`. It creates `.agent-board/` and appends
   the agents block to `AGENTS.md`, symlinking `CLAUDE.md` to it if absent.
4. **Cut the old instructions.** Remove every hit from step 1 in the instruction
   files. Replace the "read your inbox first" lines with the standing-role text from
   the README, naming the role file.
5. **Move the role files.** `.agent-bridge/ENGINEER.md` and `REVIEWER.md` become
   `docs/roles/engineer.md` and `docs/roles/reviewer.md`, rewritten in board
   vocabulary. The distilled contracts below are a starting point.
6. **Move the memory files.** `.agent-bridge/memory/*.md` to `docs/roles/memory/`.
   Keep their header rules; they are the valuable part.
7. **Open tickets for in-flight work.** One `board new` per active bridge task. The
   description must carry the open questions and the review scope verbatim from the
   thread, not a summary of them, plus the branch and the head commit under review.
8. **Move `codex-log.sh` and `prehandoff.py`** out of `.agent-bridge/tools` if you use
   them. Then delete `.agent-bridge/`.
9. **Commit `.agent-board/`.**
10. **Worktrees and the board root.** If each agent has its own worktree, export
    `AGENT_BOARD_ROOT=/abs/path/to/.agent-board` **in that agent's pane or workspace,
    never in a global shell profile.** Every board command prints the override to
    stderr, but a profile-wide value would route client B's coordination into client
    A's board with only that line to notice it by. See the sandbox note in Recipes:
    the variable picks the path, it does not grant write permission.
11. **Launch the agents.** Recipe below.

## Role files, distilled

The bridge's two role files are long because they record every lesson from a year
of rounds. Most of it survives a change of tool. Rewritten for the board:

### Reviewer

- **Entry points.** At session start, read your role file and your memory file. At
  the end of a round, append to your memory file what you verified that a cold
  session would otherwise re-derive. The human's standing-role message names both
  files; moving them into `docs/roles/` does not make anyone read them.
- Standing role: watch `review`. When a ticket appears, `board show <id>`, find the
  handoff comment, review the branch and commit it names, run the suite against that
  commit, post the verdict with `board comment <id> "..." --by <you>`, then
  `board move <id> done` or `board move <id> blocked` with what must change.
- **The ticket's acceptance criteria are the specification.** Check them one by one.
  An unverifiable criterion is a defect in the ticket; say so and block.
- Review the commit you were asked about, and say which one you reviewed. If the
  head moved, say the review is stale.
- Verify, do not accept. Re-run a cited mutation. **Do not modify the submitted
  implementation; do mutation checks in an isolated copy.** In the rehearsal Codex
  snapshotted the branch with `git archive` into a temp directory and ran the tests
  there, so the shared working tree was never touched (in the files: the verdicts
  say so).
- Name the mechanism, not just the defect. "Unreachable because X already refused
  that state" survives a refactor; "unreachable" does not.
- If your sandbox stops you verifying what you were asked to verify, say so and move
  the ticket to `blocked`. Do not approve on inspection alone.
- Contradict freely, retract freely. A retraction is more useful than consistency.
- You are the only one who moves an implementation ticket to `done`. An agent that can
  mark its own work finished has no gate. A design ruling is different: whoever
  records the agreed ruling closes it, as happened on rehearsal ticket 3.

### Engineer

- **Entry points.** At session start, `board list blocked` and `board list doing`
  for your own tickets, then your memory file. Until `board inbox` exists, `blocked`
  is where rejected work waits for you.
- Take work with `board take <id> --owner <you> --from todo`, so a ticket someone
  else already claimed is refused rather than stolen.
- Scale review rounds to risk. A migration or a public interface gets its own round
  before merge; a two-line fix goes with its task. Every round scoped, saying what not
  to review.
- Every handoff comment names the branch, the head commit, what changed, what you
  ran and what it said, and the one claim you are least sure of. The reviewer finds
  this comment, not "the latest one", so make it findable: start it with "Handoff".
- **After a rejection**, the ticket is in `blocked` with the findings. Fix, re-post a
  handoff comment with the new commit, and move it back to `review`.
- **Before merging, check the approved commit is the commit you are merging.** An
  approval names a hash; if the branch moved since, ask again.
- Pair a review round with a generative ask when the design is still open: what would
  you cut, is the frame right, find a mutation my tests do not catch.
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
| Read | At the start of every round, by name, in the standing-role message | At the start of every session |

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

`-a never` means never stop for approval; `-s workspace-write` means it may write
inside its working directory. `--full-auto` does not exist as a flag in Codex 0.153.
cmux types `--command` into the new terminal for you, so it is the sanctioned way to
start a program in a pane; during the rehearsal Claude Code's permission classifier
refused to let Claude type the same thing into someone else's pane by hand, which is
the right call. The login shell reads `.zshenv` and `.zprofile`, not `.zshrc`, so if
`board` is only on your `PATH` through `.zshrc`, either move that line or use `-lic`.
Codex asks two startup questions, trust this directory and trust hooks, that need
keypresses. Then give it the standing role once, in plain English, naming its role
file and memory file, and nudge it with "Anything in review?" from then on.

### A shared board outside the worktree needs write permission

`AGENT_BOARD_ROOT` picks the path. It grants nothing. A reviewer launched in its own
worktree under `-s workspace-write` can read a board that lives in another directory
and cannot append a comment or move a ticket there. Either launch the agent from a
directory that contains the board, or add the board to Codex's writable roots in
`~/.codex/config.toml`:

```toml
[sandbox_workspace_write]
writable_roots = ["/abs/path/to/.agent-board"]
```

Check the key name against your Codex version's `codex --help` config notes before
relying on it. Claude Code has the equivalent in its permission settings.

### The reviewer nudges back

Tell the reviewer once:

> After you have posted the verdict **and** moved the ticket, run `cmux send
> --workspace <ws> --surface <surface> "<project>: ticket N reviewed by codex"` and
> then `cmux send-key --workspace <ws> --surface <surface> enter`. Do not put your
> answer in the nudge; the board carries it.

Nudge after both actions, or the engineer reads the comment before the move and sees
a ticket still in `review`. Name the project, because the same engineer may be
running two. Find the target with `cmux identify --json` in the engineer's pane.
Reported: Codex ran both commands from inside its `workspace-write` sandbox and the
nudge arrived as a user turn in Claude's session. A successful send says the text was
delivered to the pane, not that the agent acted on it; the board says that.

### Headless reviewer, when there is no pane

```bash
cd /abs/path/to/project
before=$(board list review --json | python3 -c 'import json,sys; print({t["id"]: len(t["comments"]) for t in json.load(sys.stdin)})')
codex exec -C "$PWD" -s workspace-write \
  "Do not upgrade any tooling and do not ask questions; this is non-interactive.
   You are the reviewer; read docs/roles/reviewer.md. Run board list review, review
   each ticket there, post your verdict with board comment --by codex, and move it to
   done or blocked."
rc=$?
after=$(board list --json | python3 -c 'import json,sys; print({t["id"]: len(t["comments"]) for t in json.load(sys.stdin)})')
echo "exit $rc"; echo "before $before"; echo "after  $after"
```

A review happened only when every ticket that was in `review` before now has one
more comment and has left the column. Check that per ticket; **exit code 0 is not a
review**, and neither is "the board changed", because an unrelated new ticket
changes it too. The first version of this recipe counted files newer than `.lock`;
the lock's mtime never moves after init, so that counted every ticket on the board,
forever. Codex caught it in review. Say what the round is and forbid detours,
because a bare "go" once let a tooling version check ask a question, the
non-interactive session exited 0, and nothing was reviewed. Use `-s
danger-full-access` only when the review must run Docker or a database.

### Worktrees

`build.sh` handed a ticket to Codex in its own worktree branched from `origin/main`,
because two agents editing one working copy produce a file with half of each change
and no record of the loss. The rehearsal ran both agents in one checkout and it
worked only because they never edited at the same time, and because board moves made
on a feature branch had to be committed on `main` separately. Give each agent a
worktree, point them all at one board with `AGENT_BOARD_ROOT` set per pane, grant
the board as a writable root, and remember what the worktree does not isolate: a
shared database. `build.sh` learned that the hard way when one agent's migration
appeared in the other's schema test.

## Left behind, on purpose

| Not brought forward | Why |
|---|---|
| Registry, per-agent inbox directories, state files, unread counters | The bridge's own "what needs me" tool ignored all of them, and they split by alias once. State that nothing reads is state that lies. |
| Message types | Twelve plus thirteen ad hoc ones reduced to two facts: does this expect a reply, and which messages does it answer. |
| `SUMMARY.json` | It copied task metadata and the last five summaries. It never preserved an argument. |
| Monthly archive moves | `done` is a column. Moving files out of the hot path solved a problem the board does not have. |
| Message schema files | Never enforced in the bridge and nothing broke. |
| Tracker synchronisation inside the coordination tool | Two records of one fact drift. If the project has a tracker, the human or the engineer updates it deliberately, as the bridge's role file already required. |
| Role names as aliases (`claude` for `engineer`) | Choose a name once and use it. The alias map existed because the rename happened mid-flight. |

## After threads land

Candidates from the bridge that become small once `board inbox` exists, recorded on
this repo's own board as ticket 010:

- **Two inbox sections, not one.** Pending asks answer "what do I owe". They do not
  answer "what came back to me": an approval is a reply with no `ask`, so the
  engineer who asked for review sees an empty inbox and cannot tell acceptance from
  silence. The spec now adds a second section, asks you posted that were answered and
  that you have not posted after. Codex found this reviewing this page.
- `board inbox <you> --wait`: block until either section changes for a name, print,
  exit. `wait-inbox.sh` and `bridge-watch.sh` both did this, and both learned that a
  reply landing while nobody watches must still be reported later. Over both
  sections it is a poll loop and nothing else.
- The turn-end hook, running `board inbox <you> --json` and emitting a system message
  when either section is non-empty. Deferred until then, for the reason in the table.
- An importer for archived `.agent-bridge` thread JSON into board threads, for the
  two projects with history worth keeping.
