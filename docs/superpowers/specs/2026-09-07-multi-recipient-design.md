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
4. An unusable recipient token is dropped rather than becoming a name, and never
   voids the recipients beside it that parsed cleanly.

## Non-goals

- **No roster, no validation.** Whether a name is "real" is out of scope and stays
  so while the roster decision is unresolved. This spec must not depend on it.
- **No groups, aliases, or `--to all`.** A name that expands to other names is an
  alias map, which `decisions.md` rejects.
- **No delivery, no notification.** The board still tells nobody anything. The
  poster nudges, content-free, as now.
- **No stored per-recipient state.** Nothing records who has answered. Per-recipient
  pendingness is *derived* from the message log at read time, which an earlier draft
  of this spec wrongly assumed was impossible.

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

One helper, `parse_recipients`, does this and is shared by `_parse_trailers`
(reading files) and `_prepare_comment` (writing them), so the HTTP and CLI paths
cannot drift. The web POST handler keeps passing one raw string through to
`add_comment`/`create_thread`; it does not parse recipients itself.

**Bad tokens are dropped individually; good ones survive.** This deliberately
differs from `re`, where one non-numeric token discards the whole list, and the
asymmetry is the point rather than an oversight. Discarding a whole recipient list
leaves an `ask` addressed to nobody, which is precisely the silent defect this spec
exists to remove. Dropping only the unusable tokens keeps the message routed to
whoever was named legibly. The rest of the docstring contract is unchanged: an
unknown key is ignored, and a duplicate key keeps its first value.

`Comment.to` changes from `str | None` to `list[str]`, defaulting to an empty list.
An empty list means unaddressed, replacing the current `None`.

**A name can no longer contain a comma.** That is the point of the change, and it
is a behaviour change on existing data: a stored `to claude, codex` currently means
one recipient and afterwards means two. That is the fix, and thread 015 is the only
occurrence on this board. Any genuine comma in a name must be renamed; the release
note should say so.

## The inbox rule

Narrowed to name the answerer, not just the message:

> An addressed message carrying `ask` is pending **for recipient `R`** when `R` is
> one of its recipients and no later message **by `R`** lists its number in `re`.
>
> A later message **by the original asker** listing that number in `re` cancels the
> ask for every recipient. That is the existing "never mind" behaviour, preserved.

This needs no stored state. Today `pending_asks` computes one answered set of
message numbers; it now computes a set of `(number, answerer)` pairs from the same
single pass, plus the asker-cancelled numbers. Both are derived from the log every
time it is read, so nothing can go stale and a hand-edited file still yields the
right answer.

An earlier draft of this spec said a `re` from any recipient discharges the ask for
all of them, on the grounds that per-recipient tracking would need per-recipient
state. That was wrong twice: the state is derivable, and the rule contradicted this
spec's own Goal 2. Thread 015 on this board is the counter-example. Under
first-response-wins, codex's reply at 06:01:38 would have removed the question from
claude's inbox before claude answered at 06:05:38, recreating the exact defect for
every respondent after the first. A poll is the case where every answer matters.

`pending_asks(t, name)` returns asks still outstanding for `name`.
`pending_asks(t, None)` keeps an ask listed while **any** recipient remains.

Its return shape is unchanged, `(n, Comment)`, and **the parsed `Comment` is never
mutated**: `c.to` always holds the addressed list as written. Who still owes an
answer is a separate derivation, `remaining_recipients(t, n) -> list[str]`, so the
JSON surfaces keep reporting what was addressed while the human-facing views report
what is outstanding. `inbox_rows` therefore carries both: `to` as addressed, and a
new `waiting_on` key. The CLI printer and the Waiting strip render `waiting_on`.

### Several answers to one ask

`answered_unseen` gains an explicit ruling, because one ask can now receive several
replies. It currently emits only the latest answer per ask, so two answers arriving
before the asker next posts would collapse into one row and the earlier one would
be silently dropped — the same class of bug as the one being fixed.

It therefore emits **one row per (ask, answer-message) pair.** Two answers to one
ask produce two rows. One reply carrying `re 2,3` answers two asks and produces two
rows with the same answer number and different ask numbers, so an implementer
deduplicating by answer cannot silently hide a referenced ask. Its selector is
unchanged: asks are still found by `c.by`, and `to` is never read.

## CLI

`--to` accepts a comma-separated list, matching `--re`:

```
board comment 015 "Polling: A, B or C?" --by human --to claude,codex --ask
board thread "Design review" "..." --by human --to claude,codex --ask
```

Help text becomes `--to NAME[,NAME...]`. A name may contain spaces, so quoting is
the caller's business: `--to "Andy Smith,codex"`.

`--ask` continues to require at least one recipient, and now fails when every token
sanitises away. Today `--to ',,,' --ask` is accepted and stores `to = ',,,'`,
verified on this checkout, which is the silent-misdelivery case reached without
even a typo.

`to` changes from scalar-or-null to an array in **every** JSON surface, not just
`inbox --json`. `ticket_to_dict` serialises through `asdict` (`board.py:539`), so
`show --json`, `list --json` and `threads --json` change too wherever comments
appear. All four need documenting and testing.

This is a public JSON compatibility break with no known consumer.
`docs/migrating-from-agent-bridge.md:41` does **not** contain a hook that reads
`to`; it says only that a future integration could query whether
`board inbox <you> --json` is non-empty, which an array does not affect. An earlier
draft of this spec claimed the recipe breaks. It does not.

## Web UI

- One `to` badge per recipient, each escaped separately, so a name cannot inject
  markup through the comma path.
- The Waiting strip lists all recipients for an entry, comma-joined.
- The reply form's `to` field takes the same comma-separated text. The POST handler
  keeps passing that one raw string through to `add_comment` / `create_thread`
  unchanged; splitting and sanitising happen once, inside `_prepare_comment` via
  `parse_recipients`. The HTTP and CLI paths therefore cannot drift, and no
  recipient parsing is duplicated in the request handler.

## Testing

- `to a` parses to `["a"]`; an absent `to` yields `[]`
- `to a,b,c` parses to three recipients, in order
- rendering round-trips: `["a","b"]` renders `to a,b` and re-parses identically
- an old file containing `to claude` is unchanged in meaning
- **the reported case**: `to claude, codex` with a space yields two recipients, and
  `pending_asks(t, "claude")` and `pending_asks(t, "codex")` each return it
- **the 015 regression**: one ask to two recipients appears in both inboxes; a `re`
  from the first clears it for that one only and leaves it pending for the second
- a `re` from the original asker cancels the ask for every recipient
- `pending_asks(t, None)` lists the ask while any recipient remains, and names only
  the remaining ones
- two answers to one ask before the asker posts yield two `answered_unseen` rows,
  not one
- `to a,,b` keeps `a` and `b`; `to ,,,` yields none, and with `--ask` is refused
- all four JSON surfaces emit an array: `inbox`, `show`, `list`, `threads`
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
- An ask is pending per recipient, cleared by a `re` from that recipient, and
  cancelled for everyone by a `re` from the asker. Derived from the log; no stored
  state. The first draft's first-response-wins rule is rejected: it contradicted
  Goal 2 and would have broken thread 015.
- `answered_unseen` emits one row per (ask, answer-message) pair, so neither a
  second answer to one ask nor a second ask answered by one reply is hidden.
- Malformed recipient tokens are dropped individually rather than voiding the list,
  unlike `re`, because a voided list leaves an ask addressed to nobody.
- No groups, no aliases, no `--to all`, no validation of names against anything.
- The single-name `to` in the threads-and-inbox spec is superseded.

## Open for later

- Warning on a recipient that is not in the roster. Belongs with the roster
  decision, not here, and this spec deliberately does not depend on it.
