# agent-board — the agent roster

2026-09-07

The human declares which agents work on a project. `.agent-board/agents` holds the
declaration, `board agent` maintains it, and agents read it by running
`board agent list`. Separately and unconditionally, the agents block gains a
paragraph telling agents not to start other agents.

**This supersedes a ruling.** An earlier draft argued the roster was not a reversal
of `docs/decisions.md:98`. Codex called that rationalisation and was right. The
reversal is recorded here and in `decisions.md` rather than argued away.

## The superseded ruling

`docs/decisions.md:98` reads:

> | Names are free strings; no roster, no aliases | The claude/engineer split came
> from renaming mid-flight. The fix is choosing a name once. |

The replacement ruling, to be appended to `decisions.md`:

> **Supersedes the 2026-09-05 no-roster ruling:** the board may store a
> human-maintained advisory mapping of agent name to standing role and expose it
> through `board agent list`. It remains non-authoritative: addressing stays
> free-form; the roster neither asserts nor detects liveness, and agents never
> write to it.

Two consequences that must be applied everywhere the old wording appears:

- **"The board never knows which agents exist" becomes "the board never knows which
  agents are running."** The inventory, verified rather than remembered:
  `AGENTS.md:14`; `README.md:255` and also `README.md:114`, which says the same
  thing in different words ("does not know which agents exist"); and
  `docs/decisions.md:12`, whose registry row says "You never need to know who
  exists, only what is unclaimed", which is the sentence the roster actually
  contradicts. `CLAUDE.md` is a **symlink to `AGENTS.md`** in this checkout, so it
  is one file, not two.
- The blog source `docs/blog/2026-09-06-*.md` is `published: false`, but it repeats
  the registry row verbatim and its generated `preview.html` does too. Both need the
  row reworded, and the artifact republished, before the post goes out.
- **Current guidance must not claim the no-roster ruling still stands.** That means
  `README.md`, `AGENTS.md`, `docs/decisions.md` and the active specs, which carry a
  superseded note. Historical plans under `docs/superpowers/plans/` are deliberately
  left alone: they record what was true when written, and rewriting them would
  destroy the audit trail this project exists to keep.

What is *not* reversed, and each still stated in `decisions.md`: runtime
registration, presence, heartbeats, expiry, leases, and scheduling. Avoiding
heartbeats answers the registry objection; it never answered the roster objection,
which is why this is a supersession and not a clarification.

## Why

**Agents start other agents.** Observed repeatedly. They were not disobeying: the
block explains how to *talk to* another agent and refers to "the next agent", but
never says do not create one.

**A cold agent has no `--to` target.** On a board with messages, names are
derivable from existing `by` and `to` trailers. On an empty board there is nothing
to derive, which is exactly when a new agent is deciding what to do.

The rehearsal in `README.md` is the third data point: a nudged Codex found the
review ticket, read it, and reviewed nothing, because the block is role-neutral and
nothing had told it whose job the review column was.

**These are two problems, and only the second needs a roster.** The prohibition
fixes the first on its own, so it is unconditional and does not depend on a roster
existing. The roster addresses ignorance of who is here, not disobedience. Both
depend on the agent obeying, so this **addresses** the failure; it does not
**prevent** it. Prevention is a launcher concern — disable the spawn tool — and out
of scope.

## Goals

1. A human declares agents: name and optional role.
2. Agents can read the declaration with one command.
3. The block tells agents not to start other agents **whether or not a roster
   exists**, and says what to do instead.
4. Nothing an agent does writes to the roster.
5. One source of truth, so nothing can drift and there is no reconcile step.

## Non-goals

- **Liveness, presence, heartbeats, ping, expiry.** The board does not learn about
  processes. `pgrep` and the terminal multiplexer already answer that correctly.
- **A minimum agent count.** A one-agent board with a human reviewer is valid, and
  an empty roster is legal.
- **Hard validation of `--to`.** Names stay free-form. A warning is deferred; see
  Open for later.
- **Reserved vendor names.** `claude` and `codex` are seeded defaults only.
- **Groups or aliases.** A name that expands to other names is the alias map
  `decisions.md` still rejects.

## One source of truth

`.agent-board/agents` is the roster. **The agents block does not contain the
names.** It points at the command:

    board agent list                  who works on this project

This is the shape Codex proposed, and it follows `decisions.md:17` — *"the
directory is the only truth, so nothing can drift and there is no reconcile
step."* An earlier draft embedded the names in `AGENTS.md`, which created two
copies with no repair path: a hand edit would not re-render, and a crash could land
the roster write and not the doc rewrite.

Because nothing is projected, a direct human edit of `.agent-board/agents` takes
effect immediately and there is no `sync` command. Roster mutations never touch
`AGENTS.md` or `CLAUDE.md`. `--no-agents` still needs a stated meaning, because
seeding writes inside `.agent-board/`; see Seeding below.

The block already establishes this pattern: `board inbox <you>` is annotated *"Run
this first."*

## Storage and the parsing contract

One entry per line, name and role separated by the **first** tab:

```
claude	engineer
codex	reviewer
Andy Smith	engineer
solo
```

A missing file is an empty roster, not an error.

Reading:

- A line whose first non-space character is `#` is a comment. A blank line is
  skipped.
- Split on the **first** tab only. Text after it is the role, and may contain tabs,
  which collapse to spaces on write.
- The name is sanitised with `sanitize_name` (one line, no `·`, control characters
  collapsed) so a roster entry can never forge a message header. The role is
  sanitised with `sanitize_scalar`; the `·` restriction exists for header names and
  does not apply to roles.
- A line whose name sanitises to empty is skipped.
- **Two entries whose names differ only by case is an error**, reported by
  `board agent list` and by any mutation, naming both lines. Silently picking one
  would make `board inbox` ambiguous. The message says which lines and that a human
  must resolve it.

Writing:

- **A name may not contain a comma.** `to` is a comma-separated list as of
  2026-09-07, so a comma in a roster name would advertise a recipient that can
  never be addressed. Rejected on CLI writes with that reason, and skipped with a
  warning when read from a hand-edited file, so the roster cannot list an
  unaddressable name.
- A name may not begin with `#`, because such a line would be read back as a
  comment. `sanitize_name('#reviewer')` returns `'#reviewer'` unchanged, verified,
  so the CLI must reject it explicitly with that reason.
- A name may not be empty after sanitising.
- A name may not contain a tab; `sanitize_name` already collapses one to a space,
  so this is automatic and tested rather than enforced.
- **CLI mutations canonicalise the file and discard comments and blank lines.** A
  mutation is a read-modify-write and preserving annotations through it is not
  worth the machinery. `board agent list` says so, and the file carries a generated
  header line saying edits by hand survive but comments do not survive the next
  `board agent` command.
- Entry order is preserved; a new entry appends.

Every mutation takes `board_lock` and writes atomically via a temporary file and
`os.replace`, so a concurrent reader never sees a partial roster.

## CLI

```
board agent add <name> [--role ROLE]     add, or replace the role of an existing name
board agent remove <name>...             remove one or more entries
board agent list                         print the roster
board agent clear --all                  empty the roster
```

`clean` is gone. Codex called the omitted-argument-means-wipe-everything form a
footgun and it was: `remove` already covers every case a human hits, and destroying
the file now requires typing `--all`. `clear` without `--all` is an error that says
what to type.

`add` on an existing name replaces its role, matched case-insensitively, keeping
the stored spelling of the existing entry so a name does not silently change case.
`remove` on an absent name is not an error. `list` on an empty roster prints
`(no agents declared)`.

None of these inspect processes. Liveness reporting, if ever wanted, is a separate
read-only command; see Open for later.

## Seeding

`board init` seeds `claude — engineer` and `codex — reviewer` **only when it
creates `.agent-board/` for the first time.** On an existing board — the upgrade
path `README.md:35` tells users to take — a missing `agents` file stays missing.

Without that condition, rerunning `init` to pick up a new `board.py` would silently
declare two agents that may not exist on that project, which would also undercut
the "written by a human, deliberately" claim the superseding ruling rests on.

**`board init --no-agents` seeds nothing.** The flag's help says "only create
`.agent-board/`; do not touch `AGENTS.md` or `CLAUDE.md`", and a roster lives inside
`.agent-board/`, so the literal reading would still seed. It does not, because the
flag exists for an operator wiring the agent-facing setup themselves, and that
operator wants to declare their own roster. A seeded roster nobody asked for is the
same "not written by a human" problem in a different file. Tested explicitly for
fresh-init-with-the-flag.

## The agents block

`agents_block()` gains two things, both **unconditional** and neither depending on
a roster:

```
    board agent list                  who works on this project
```

and, as its own paragraph:

```markdown
**Do not start other agents.** The agents on this project are started by the
human, and one is probably already running. Never run `claude`, `codex`, or a
spawn/subagent tool yourself. If a task needs an agent that is not listed by
`board agent list`, put a ticket in `todo/` describing it and say so in your
reply.
```

Unconditional matters: an empty roster is legal, an agent is plainly running even
then, and gating the prohibition on a non-empty roster would leave the motivating
failure unfixed on every existing board. Codex raised this and it was the clearest
error in the first draft.

Because the block is now the same for every project regardless of roster contents,
the existing golden-output guarantee is easy to keep: the block changes exactly
once, when this feature lands, and never again as the roster changes.

## Testing

- round-trip: add, list, remove, remove several, `clear --all`, missing file
- `add` on an existing name replaces the role, keeps the stored spelling, does not
  duplicate the line
- case: added `Andy`, replaced by `board agent add andy --role x`, one entry remains
  spelled `Andy`
- case-differing duplicates in a hand-edited file are reported as an error by
  `list` and by a mutation, naming both lines
- first-tab split: a role containing a tab is preserved as role text
- a name beginning with `#` is rejected on write, with the round-trip reason
- an empty name after sanitising is rejected
- a name containing `·` is sanitised and cannot forge a message header
- comments and blank lines are ignored on read, and discarded by a mutation, and
  that is asserted rather than incidental
- entries whose name sanitises to empty are skipped on read
- `clear` without `--all` errors and changes nothing
- seeding: a fresh `init` creates two entries; `init` on an existing board with no
  `agents` file creates none — the upgrade case, asserted explicitly
- the block contains the prohibition and the `board agent list` line with an empty
  roster, a seeded roster, and a cleared roster — identical output in all three
- roster mutations leave `AGENTS.md` and `CLAUDE.md` byte-identical
- concurrent `board agent add` calls serialise and neither entry is lost
- an interrupted write leaves the previous roster intact, not a partial file

`TestAgentsDoc::test_block_is_role_neutral` keeps its assertion but is renamed: the
block is still role-neutral, and now also carries a prohibition, so the old name
overclaims what it checks. A golden-string test pins the new block exactly.

## Decisions to record

- Supersedes the no-roster ruling, in the wording quoted above. The registry,
  presence, expiry, lease and scheduler rulings are unchanged.
- The invariant becomes "the board never knows which agents are **running**."
- One source of truth: the roster is not projected into `AGENTS.md`, so there is
  nothing to reconcile and a hand edit takes effect at once.
- The spawn prohibition is unconditional and independent of the roster. It
  addresses the failure; it does not prevent it.
- `clean` rejected in favour of `remove <name>...` plus `clear --all`.
- `init` seeds only on first creation of `.agent-board/`.
- CLI mutations canonicalise and discard comments.
- Case-differing duplicate names are an error, never silently resolved.
- No minimum agent count. Vendor names are defaults, not reserved. Swapping the
  model behind a role needs no board change **only if the human keeps the same
  board name and tells the replacement model that name**; the seeded names look
  like vendor identity, so swapping those two normally does mean a roster edit.

## Open for later

- **A warning when `--to` names someone not in the roster.** Deferred so the
  messaging path does not depend on the roster. Belongs with, or after, the
  multi-recipient change, where the check would run per name.
- **`board agent check`**, read-only liveness reporting, if "is anyone going to
  pick this up" ever becomes a real question. Shape fixed: reports, never edits.
- **Presence via a held `flock`** on a per-agent file, which gives kernel-accurate
  liveness with no heartbeat and no cleanup, verified through `kill -9`. Not built,
  because the problem in hand was agents spawning agents.
