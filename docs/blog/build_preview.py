import base64, pathlib, re

REPO = pathlib.Path(__file__).resolve().parents[2]
IMG = REPO / "docs/blog/images"
OUT = REPO / "docs/blog/preview.html"


def data_uri(name: str) -> str:
    raw = (IMG / name).read_bytes()
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


BOARD = data_uri("01-board-top.png")
WAIT = data_uri("03-waiting-strip.png")

HTML = """<title>Deleting the Orchestrator</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,400;6..72,600&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
:root {
  --ground: #f6f7f8;
  --raised: #ffffff;
  --ink: #16191d;
  --muted: #5b646e;
  --rule: #dde1e5;
  --accent: #b26a00;
  --accent-soft: #f5e6cc;
  --code-ground: #eef0f2;
  --code-ink: #22272c;
  --quote-ground: #f0f2f3;
  --shadow: 0 1px 2px rgba(20,25,30,.06), 0 8px 24px rgba(20,25,30,.05);
  --serif: "Newsreader", Georgia, "Times New Roman", serif;
  --sans: "IBM Plex Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, monospace;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --ground: #14171a;
    --raised: #1b1f24;
    --ink: #e6e9ec;
    --muted: #939da7;
    --rule: #2a3037;
    --accent: #e0a44a;
    --accent-soft: #3a2e18;
    --code-ground: #1b1f24;
    --code-ink: #d6dbe0;
    --quote-ground: #1a1e23;
    --shadow: 0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.3);
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --ground: #14171a;
  --raised: #1b1f24;
  --ink: #e6e9ec;
  --muted: #939da7;
  --rule: #2a3037;
  --accent: #e0a44a;
  --accent-soft: #3a2e18;
  --code-ground: #1b1f24;
  --code-ink: #d6dbe0;
  --quote-ground: #1a1e23;
  --shadow: 0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.3);
  color-scheme: dark;
}

body {
  background: var(--ground);
  color: var(--ink);
  font-family: var(--sans);
  font-size: 17px;
  line-height: 1.65;
  -webkit-font-smoothing: antialiased;
}
.page { max-width: 42rem; margin: 0 auto; padding: 4rem 1.25rem 6rem; display: flex; flex-direction: column; gap: 1.5rem; }

.eyebrow {
  font-family: var(--mono);
  font-size: .72rem;
  letter-spacing: .1em;
  text-transform: uppercase;
  color: var(--muted);
  display: flex; flex-wrap: wrap; gap: .5rem 1rem; align-items: baseline;
}
.eyebrow .dot { color: var(--rule); }

h1 {
  font-family: var(--serif);
  font-weight: 600;
  font-size: clamp(2rem, 5.5vw, 2.9rem);
  line-height: 1.12;
  letter-spacing: -.015em;
  text-wrap: balance;
  margin: 0;
}
.standfirst {
  font-family: var(--serif);
  font-size: 1.2rem;
  line-height: 1.55;
  color: var(--muted);
  margin: 0;
  text-wrap: pretty;
}
hr.sep { border: 0; border-top: 1px solid var(--rule); margin: .75rem 0; width: 100%; }

h2 {
  font-family: var(--serif);
  font-weight: 600;
  font-size: 1.55rem;
  line-height: 1.25;
  letter-spacing: -.01em;
  text-wrap: balance;
  margin: 2.25rem 0 0;
}
p { margin: 0; text-wrap: pretty; }
strong { font-weight: 600; }
a { color: var(--accent); text-underline-offset: .18em; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; border-radius: 2px; }

.rule-stmt {
  font-family: var(--serif);
  font-size: 1.3rem;
  line-height: 1.4;
  margin: .5rem 0;
  padding: 1rem 0 1rem 1.25rem;
  border-left: 3px solid var(--accent);
  color: var(--ink);
  text-wrap: pretty;
}

blockquote {
  margin: 0;
  padding: 1rem 1.15rem;
  background: var(--quote-ground);
  border-left: 2px solid var(--rule);
  border-radius: 0 4px 4px 0;
  color: var(--muted);
  font-size: .95rem;
  display: flex; flex-direction: column; gap: .7rem;
}
blockquote strong { color: var(--ink); }

pre {
  margin: 0;
  background: var(--code-ground);
  color: var(--code-ink);
  font-family: var(--mono);
  font-size: .82rem;
  line-height: 1.6;
  padding: 1rem 1.15rem;
  border-radius: 5px;
  overflow-x: auto;
  border: 1px solid var(--rule);
}
code { font-family: var(--mono); font-size: .88em; }
p code, li code, td code {
  background: var(--code-ground);
  padding: .1em .35em;
  border-radius: 3px;
}

.msghdr {
  font-family: var(--mono); font-size: .78rem; line-height: 2.1;
  background: var(--raised); border: 1px solid var(--rule);
  border-radius: 5px; padding: .9rem 1.1rem; overflow-x: auto;
  box-shadow: var(--shadow); color: var(--ink);
}
.msghdr .sep2 { color: var(--rule); padding: 0 .15rem; }
.badge {
  display: inline-block; font-family: var(--mono); font-size: .72rem;
  padding: .1em .45em; border-radius: 3px; white-space: nowrap;
  background: var(--code-ground); color: var(--muted); border: 1px solid var(--rule);
}
.badge.ask { background: var(--accent-soft); color: var(--accent); border-color: transparent; font-weight: 500; }
.msghdr .body { color: var(--muted); display: block; margin-top: .4rem; }

figure { margin: 1rem 0; display: flex; flex-direction: column; gap: .6rem; }
figure img {
  width: 100%; display: block; border-radius: 6px;
  border: 1px solid var(--rule); box-shadow: var(--shadow);
}
figcaption { font-size: .82rem; color: var(--muted); font-family: var(--sans); }

.wide { width: min(52rem, 92vw); margin-left: 50%; transform: translateX(-50%); }

.tablewrap { overflow-x: auto; border: 1px solid var(--rule); border-radius: 5px; background: var(--raised); }
table { border-collapse: collapse; width: 100%; font-size: .88rem; }
th, td { text-align: left; padding: .7rem .9rem; border-bottom: 1px solid var(--rule); vertical-align: top; }
th { font-family: var(--mono); font-size: .7rem; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); font-weight: 500; }
tbody tr:last-child td { border-bottom: 0; }
td:first-child { font-weight: 500; }

ol, ul { margin: 0; padding-left: 1.3rem; display: flex; flex-direction: column; gap: .7rem; }
li { padding-left: .2rem; }

.endnote {
  font-size: .88rem; color: var(--muted);
  border-top: 1px solid var(--rule); padding-top: 1.25rem; margin-top: 1.5rem;
}
@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
</style>

<article class="page">

  <div class="eyebrow">
    <span>6 September 2026</span><span class="dot">/</span>
    <span>1,712 words</span><span class="dot">/</span>
    <span>agent-board</span>
  </div>

  <h1>Your agent orchestrator is a black box. Mine is a folder.</h1>

  <p class="standfirst">Coordinate two coding agents with directories and markdown files. No scheduler, no runtime registry, no tokens spent deciding who does the work.</p>

  <hr class="sep">

  <p>There is a moment with every agent orchestration framework where you look at the token bill, then at what actually got built, and cannot account for the gap.</p>

  <p>You know the shape of it. A framework with tens of thousands of stars. A supervisor agent that decides which worker agent should act. A planner that re-plans. Some of your spend went to writing code, and some went to a model thinking about which model should think about the code, and you cannot separate the two because the interesting part happened inside a Python object that no longer exists.</p>

  <p>I got tired of that. Not of agents, which work fine. Of not being able to answer three questions:</p>

  <ol>
    <li>What is each agent doing right now?</li>
    <li>Who is waiting on whom?</li>
    <li>What did they decide, and why?</li>
  </ol>

  <p>Every framework I tried answered those with a dashboard, a trace viewer, or nothing. I wanted to answer them with <code>ls</code>.</p>

  <h2>What you get</h2>

  <p>Here is the whole idea. Your agents coordinate through a directory:</p>

<pre>.agent-board/
    todo/
        005-max-id-attempts-5-can-starve.md
    doing/
        001-comment-body-can-spoof-a-comment-header.md
    review/
        011-bare-cr-bypasses-body-escaping-and-forges-messages.md
    blocked/
    done/
    threads/
    agents          &lt;- who works here, written by you</pre>

  <p>Columns are directories. Tickets are markdown files. An agent claims work by moving a file. That is the entire data model, and you already know how to operate it, because it is <code>ls</code>, <code>mv</code>, <code>cat</code>, <code>grep</code> and <code>git log</code>.</p>

  <p>Which means:</p>

  <ul>
    <li><strong>Coordination costs zero tokens.</strong> Nothing calls a model to decide who works next. There is no supervisor, because assignment is which folder the file is in. The only tokens you spend are the ones your agents spend doing the work.</li>
    <li><strong>You can read all of it.</strong> Not a trace viewer. The actual conversation, in your editor, in a markdown file, right now.</li>
    <li><strong>You can edit it.</strong> An agent misunderstood the ticket? Fix the sentence. No API, no migration, no restart. It is a text file.</li>
    <li><strong>Nothing runs unless you run it.</strong> No daemon, no background scheduler, no process to babysit or pay for.</li>
    <li><strong>Your reviewer's argument is a diff.</strong> When two agents disagree and one changes its mind, that is in <code>git log</code> a year later.</li>
  </ul>

  <p>And you can read the implementation. It is one Python file, 1,585 lines, standard library only. Not "small for a framework." Small enough that you could sit down and understand every line this afternoon, and then change the parts you disagree with.</p>

  <p>If you have Python 3.11+ on macOS or Linux, you <code>curl</code> it into a project and it works. No npm, no build step, no dependencies. It uses <code>fcntl</code> for locking, so POSIX only.</p>

  <figure class="wide">
    <img src="__BOARD__" alt="The agent-board web UI: a header reading 11 tickets, 0 threads, an amber WAITING strip showing one unanswered question from claude to codex, and five columns labelled todo, doing, review, blocked and done, each holding markdown-backed ticket cards.">
    <figcaption>The board. Five columns, one unanswered question in the strip at the top.</figcaption>
  </figure>

  <p>There is a web UI, and it owns nothing. It is a projection of the directory. Every button performs an ordinary file operation, and a hard refresh rebuilds the page from disk. Kill the server mid-sentence and you lose nothing, because there was nothing in it. Use it when you want to see the shape of things; use the CLI for everything else.</p>

  <h2>The whole protocol, in eight commands</h2>

  <p>Claude picks up a bug and hands it to Codex:</p>

<pre>board init
board new "Divide crashes on zero divisor"
board take 1 --owner claude
board comment 1 "Fixed in a1b2c3d. Guard clause plus a test." \
    --by claude --to codex --ask --commit a1b2c3d
board move 1 review</pre>

  <p>Codex, in its own terminal, asks what is waiting on it:</p>

<pre>$ board inbox codex

AWAITING YOUR REPLY (1)
  001  ticket #1   claude to codex  2026-09-06 09:12:04  a1b2c3d
        Fixed in a1b2c3d. Guard clause plus a test.</pre>

  <p>It reviews, answers, closes:</p>

<pre>board comment 1 "Approved. Guard is correct and the test covers zero." \
    --by codex --to claude --re 1
board move 1 done</pre>

  <p>That is it. Two flags carry the entire protocol. <code>--ask</code> says a reply is expected. <code>--re 1</code> says which message this answers. There is no message type, no status field, no state machine. One rule decides everything:</p>

  <p class="rule-stmt">An addressed message carrying <code>ask</code> is pending for a recipient until a later message by that recipient lists its number in <code>re</code>.</p>

  <p>Not "who spoke last." A plain message is never pending at all. Only an ask is, and only until something answers it.</p>

  <p>Everything else in this post is that rule doing its job.</p>

  <h2>The screen you will actually live in</h2>

  <figure>
    <img src="__WAIT__" alt="A narrow amber strip labelled WAITING, listing a single entry: ticket 001, message 1, from claude to codex, asking whether ticket 001 closes now or whether ticket 011 replaces it.">
    <figcaption>Every unanswered ask on the board, in one strip.</figcaption>
  </figure>

  <p>Every unanswered question on the board, across every ticket, in one strip. No unread badges, no per-agent filter, no notification service. If a question is open, it is there until something answers it. <code>board inbox</code> prints the same thing in your terminal.</p>

  <p>The first time you run it and it says <code>(nothing pending)</code>, that is not a guess about the conversation. It is a fact about the files.</p>

  <h2>What this buys you, concretely</h2>

  <p>Last week the board caught a bug in itself, and the way it happened is the best argument I have for working this way.</p>

  <p>I shipped a feature. 161 tests green. I asked Codex to review the diff, and it filed this:</p>

  <blockquote>
    <p>[P2] Normalize carriage returns before neutralising message bodies. If a body contains a bare CR before a header-shaped example, this scan misses it because it splits only on newline. <code>load_ticket()</code> subsequently normalizes carriage returns into newlines, turning the example into a real message.</p>
  </blockquote>

  <p>Messages are markdown, and a message body could contain something that looks like a message header. If it did, it would parse as a real message, and a forged <code>re</code> could mark a real question as answered. I had escaped that. Codex found the gap: files are read in text mode, where Python turns a bare <code>\r</code> into a newline <em>after</em> my escaping ran.</p>

  <p>Two minutes to reproduce:</p>

<pre>after real ask  -&gt; messages=1  pending_asks=1
after CR body   -&gt; messages=3  pending_asks=0
    #1 by='claude'  re=[]   ask=True   to='codex'
    #2 by='mallory' re=[]   ask=False  to=None
    #3 by='codex'   re=[1]  ask=False  to='claude'</pre>

  <p>Message #3 is attributed to an agent that never wrote it, and it silently cleared a real request.</p>

  <p>Here is the part that matters to you. That argument did not happen in a chat window that I would close and lose. It happened on the board, so I still have it. I pushed back on the severity, Codex moved it to P1, and then it caught me overstating my own fix:</p>

  <blockquote>
    <p>The named test only exercises bare CR around a forged header. It does <strong>not</strong> itself cover CRLF, ordinary LF, or a trailing CR as claimed.</p>
  </blockquote>

  <p>It was right. I had claimed four cases; the committed test had one. The green suite did not catch that. My reviewer did, by reading what I wrote against what I committed, and I can still pull up the exact exchange because it is a markdown file in git.</p>

  <p>That is the thing the dashboards never gave me. Not observability. Custody.</p>

  <h2>What you are not paying for</h2>

  <p>The reason this stays small is that every feature I refused to build is written down, with the reason. Condensed from <code>decisions.md</code>:</p>

  <div class="tablewrap">
    <table>
      <thead><tr><th>Rejected</th><th>Why</th></tr></thead>
      <tbody>
        <tr><td>An agent registry that agents write to</td><td>Roster state maintained at runtime goes stale. A crashed agent stays registered forever. (A list <em>you</em> write is fine — it never claims anyone is running.)</td></tr>
        <tr><td>A scheduler</td><td>Assignment is the column, or a human. An LLM scheduler is the main reason multi-agent boards get expensive.</td></tr>
        <tr><td>Leases / claim expiry</td><td>A human is in the loop and notices a stuck ticket.</td></tr>
        <tr><td>JSON tickets</td><td><code>comments[]</code> is written concurrently. JSON arrays conflict in git every time.</td></tr>
        <tr><td>SQLite</td><td>A binary blob kills <code>git log</code> for a ticket. The audit trail is worth more than query power.</td></tr>
        <tr><td>A <code>status</code> field</td><td>The directory is the only truth, so nothing can drift and there is no reconcile step.</td></tr>
        <tr><td>Presence / heartbeats</td><td>Stale presence is worse than none, because it is trusted.</td></tr>
      </tbody>
    </table>
  </div>

  <p>These are not hypotheticals. This replaced a homegrown system of mine called <code>.agent-bridge</code>, and before writing this I scanned <code>~/Source</code> rather than trust my memory: on 6 September 2026 it was installed in <strong>twelve</strong> repositories holding <strong>176 conversations</strong>, and every single copy had a <code>registry/</code> directory.</p>

  <p>The registry went stale, because nothing removes a crashed agent from a list. The status field drifted from the directory, because two places can disagree. The JSON messages conflicted in git on every concurrent write. And because the whole thing was copied per project, there were twelve versions of it, ten of which had fallen behind the two I actually maintained.</p>

  <p>Every one of those was a small, sensible yes at the time. That is how this happens. Nobody sets out to build an orchestrator. You add a registry because you need to know which agents exist, and eleven yeses later you are maintaining infrastructure instead of shipping.</p>

  <h2>Start here</h2>

<pre>curl -O https://raw.githubusercontent.com/jharjadi/agent-board/main/board.py
python3 board.py init</pre>

  <p><code>init</code> writes a block into your <code>AGENTS.md</code> and <code>CLAUDE.md</code> telling your agents the commands. They pick it up on their next run. Then give one agent a ticket and tell the other to check <code>board inbox</code>.</p>

  <p>Three things to hold onto, whether or not you use this:</p>

  <ol>
    <li><strong>Assignment should be location.</strong> A ticket in <code>review/</code> is in review. Two places that can disagree will eventually disagree.</li>
    <li><strong>Have exactly one rule for "is this answered."</strong> Not who spoke last, not a flag someone remembers to set.</li>
    <li><strong>Keep it in plain text, in git.</strong> The argument where your reviewer changed its mind is worth more in six months than the code it was about.</li>
  </ol>

  <p>The tool is one Python file with no dependencies, no server to keep alive, and no idea who you are. It is coordinating real work on a client project now, and the most interesting thing it has done so far is help me find a bug in itself.</p>

  <p>That is the bar I would hold any of these to. Not "can it orchestrate." Can it get out of the way.</p>

  <p class="endnote"><a href="https://github.com/jharjadi/agent-board">github.com/jharjadi/agent-board</a>, MIT. Every command and transcript above is from the real board. The bug is ticket 011; the fix is commit <code>98f93e0</code>. Codex reviewed this post before it went up, on the board, and found eight things wrong with it.</p>

</article>
"""

HTML = HTML.replace("__BOARD__", BOARD).replace("__WAIT__", WAIT)


def check_against_markdown() -> None:
    """The article lives twice: as markdown to paste, and as HTML in this file.
    That is two copies with no repair path, and it has already bitten once — the
    markdown was corrected while this generator kept rebuilding the old claims.
    Until the HTML is derived from the markdown, this fails loudly on drift.
    """
    md = (REPO / "docs/blog/2026-09-06-i-deleted-my-orchestrator.md").read_text()
    problems = []

    # Numbers stated in the prose must match between the two copies.
    for pattern, label in [(r"one Python file, ([\d,]+) lines", "line count"),
                           (r"(\d+) tests green", "test count")]:
        in_md = set(re.findall(pattern, md))
        in_html = set(re.findall(pattern, HTML))
        if in_md != in_html:
            problems.append("%s differs: markdown %s, html %s" % (label, in_md, in_html))

    # Claims that must not survive in either copy.
    for stale in ("no registry,", "1,364 lines",
                  "pending until a later message in the same file"):
        if stale in md or stale in HTML:
            problems.append("stale claim still present: %r" % stale)

    if problems:
        raise SystemExit("preview is out of step with the markdown:\n  "
                         + "\n  ".join(problems))


check_against_markdown()
OUT.write_text(HTML, encoding="utf-8")
print(f"wrote {OUT}  ({len(HTML)/1024:.0f} KB)  [checked against the markdown]")
