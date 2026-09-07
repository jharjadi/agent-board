# agent-board — addressing more than one recipient

2026-09-07

`to` becomes a list of names, so one message can ask several participants at once
and stay pending for each of them independently until each answers. This
supersedes the single-name `to` field defined in the threads-and-inbox spec.

It fixes a live silent defect, not a missing feature. Today `--to claude,codex` is
accepted, sanitised, rendered, and shown in the human's waiting strip, while being
pending for nobody at all.

## The defect

Reproduced on this board as thread 015, posted by the human:

```
## comment — human · 2026-09-07T05:59:09Z · to claude, codex · ask
Polling:
A. 200
B. 300
c. 400
```

```
$ board inbox                 015  human to claude, codex   "Polling:"
$ board inbox claude          (not listed)
$ board inbox codex           (nothing pending)
```

`to` holds one opaque string. `sanitize_name` collapses whitespace and strips `·`
but does not split on commas, so the value survives as a single name.
`pending_asks` compares the whole field, `c.to.lower() != want`, so neither
`claude` nor `codex` matches.

The failure is silent and asymmetric. `board inbox` with no name lists every
pending ask regardless of recipient, so the human sees the question and the web
Waiting strip shows it. Both agents see nothing. Nothing anywhere reports a
problem, and the question waits forever looking like it was ignored.

`--ask` without `--to` is already refused, which shows the intent: an ask must have
someone to answer it. An ask addressed to a name nobody holds defeats that check
while passing it.

## Why a list, and why now

The header already carries a list. `re 5,6,7` is comma-separated, parsed by
splitting on `,` inside `_parse_trailers`, and rendered by `",".join(...)` in
`render_comment_header`. `to` becoming a list follows an established shape in the
same header rather than inventing one, which is most of why this is small.

It also reverses no ruling. `docs/decisions.md` is silent on how many names an
address may carry, and nothing here teaches the board about agents, processes,
scheduling, or liveness. Addressing stays free-form: a name is still any string the
writer chooses.

The motivating use is a meeting. Three participants on one thread, the human
included, each adding a signed message to the same file. That works today except
for asking the room a single question, which is the one thing a meeting is for.

## Goals

1. One message may address several names.
2. It is pending for each addressee separately, and clears for each as that one
   answers with `re`.
3. Existing files and existing single-name usage parse and render unchanged.
4. A recipient list that cannot be parsed is treated as absent, never as a name.

## Non-goals

- **No roster, no validation.** Whether a name is "real" is out of scope and stays
  so while the roster decision is unresolved. This spec must not depend on it.
- **No groups, aliases, or `--to all`.** A name that expands to other names is an
  alias map, which `decisions.md` rejects.
- **No delivery, no notification.** The board still tells nobody anything. The
  poster nudges, content-free, as now.
- **No per-recipient state.** Pendingness is computed from the message list at read
  time. Nothing records which recipient has answered.

## Message header

```
## comment — <by> · <iso8601>[ · to <name>[,<name>...]][ · ask][ · re <n>[,<n>...]][ · commit <text>]
```

Recipients are comma-separated with no space after the comma when rendered,
matching `re`:

```
## comment — human · 2026-09-07T05:59:09Z · to claude,codex · ask
```

Anchoring is unchanged: `COMMENT_RE` matches on the timestamp, and the trailer is
everything after it, so this is not a parser change beyond the `to` key.

## Parsing

In `_parse_trailers`, `to` splits on `,`. Each token is stripped and sanitised with
`sanitize_name`; empty tokens are dropped. Duplicates are removed, keeping first
appearance, compared case-insensitively.

The existing contract in that function's docstring holds unchanged: an unknown key
is ignored, a duplicate key keeps its first value, and **a malformed known key
counts as absent, never as something else.** So a `to` whose every token sanitises
away yields no recipients rather than a junk name — the same treatment `re`
already gives a non-numeric token.

`Comment.to` changes from `str | None` to `list[str]`, defaulting to an empty list.
An empty list means unaddressed, replacing the current `None`.

**A name can no longer contain a comma.** That is the point of the change, and it
is a behaviour change on existing data: a stored `to claude, codex` currently means
one recipient and afterwards means two. That is the fix, and thread 015 is the only
occurrence on this board. Any genuine comma in a name must be renamed; the release
note should say so.

## The inbox rule

Unchanged in wording, narrowed only in how a recipient is matched:

> An addressed message carrying `ask` is pending for `name` when `name` is one of
> its recipients and no later message in the same file lists its number in `re`.

`pending_asks` replaces `c.to.lower() != want` with membership over the lowercased
recipient list. With no name given it still lists every pending ask, as now.

One ask to three people is one message and one entry in each of three inboxes. It
leaves each inbox as that recipient answers it, and a `re` from any recipient
discharges it for **everyone**, because `re` names a message and the answered set
is computed per message, not per recipient. That is deliberate: tracking which
recipient still owes an answer would be per-recipient state, which is a non-goal.
If a question genuinely needs an answer from each of three people, send three asks.
This must be stated in the README, because it is the one place the model is not
what a reader would guess.

`answered_unseen` needs no change. It matches on `c.by` to find the asks I posted
and never reads `to`.

## CLI

`--to` accepts a comma-separated list, matching `--re`:

```
board comment 015 "Polling: A, B or C?" --by human --to claude,codex --ask
board thread "Design review" "..." --by human --to claude,codex --ask
```

Help text becomes `--to NAME[,NAME...]`. A name may contain spaces, so quoting is
the caller's business: `--to "Andy Smith,codex"`.

`--ask` continues to require at least one recipient, and now fails when every token
sanitises away, which today produces the silent-misdelivery case.

`board inbox --json` emits `to` as an array. This is a breaking change for any
consumer, including the turn-end hook recipe in
`docs/migrating-from-agent-bridge.md`, which is documented but not shipped. The
migration guide needs a note.

## Web UI

- One `to` badge per recipient, each escaped separately, so a name cannot inject
  markup through the comma path.
- The Waiting strip lists all recipients for an entry, comma-joined.
- The reply form's `to` field takes the same comma-separated text. The POST handler
  currently does `(fields.get("to") or [""])[0].strip() or None`; it splits on
  commas and sanitises each token like the CLI.

## Testing

- `to a` parses to `["a"]`; an absent `to` yields `[]`
- `to a,b,c` parses to three recipients, in order
- rendering round-trips: `["a","b"]` renders `to a,b` and re-parses identically
- an old file containing `to claude` is unchanged in meaning
- **the reported case**: `to claude, codex` with a space yields two recipients, and
  `pending_asks(t, "claude")` and `pending_asks(t, "codex")` each return it
- one ask to two recipients appears in both inboxes; a `re` from either clears it
  from both, and that is asserted so the choice is pinned rather than incidental
- case: `to Claude,CODEX` is found by `claude` and `codex`
- duplicates collapse: `to a,A,a` yields one recipient
- every token sanitising away yields no recipients, and with `--ask` is refused
- a token containing `·` is sanitised and cannot end the header or start a trailer
- a name with a space survives inside a list
- web: one badge per recipient, each escaped; a recipient named
  `<script>alert(1)</script>` renders inert
- the JSON shape is a list

## Decisions to record

- `to` is a list. A name may no longer contain a comma; existing values containing
  one change meaning, which is the fix.
- A `re` from any recipient discharges the ask for all of them. Per-recipient
  answer tracking is refused as per-recipient state. Send separate asks when each
  answer matters.
- No groups, no aliases, no `--to all`, no validation of names against anything.
- The single-name `to` in the threads-and-inbox spec is superseded.

## Open for later

- Warning on a recipient that is not in the roster. Belongs with the roster
  decision, not here, and this spec deliberately does not depend on it.
