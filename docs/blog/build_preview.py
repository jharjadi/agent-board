import base64, pathlib

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
    <span>2,030 words</span><span class="dot">/</span>
    <span>agent-board</span>
  </div>

  <h1>I kept building an agent orchestrator. Then I deleted it and used a folder.</h1>

  <p class="standfirst">Twelve diverged copies of a tool whose whole job was keeping things in sync. Here is what was left after I threw it away.</p>

  <hr class="sep">

  <p>For about a year I kept building the same thing and kept being wrong about it.</p>

  <p>The thing was a way to make two coding agents work on one codebase without stepping on each other. Claude writes the code, Codex reviews it, a human stays in the loop. That is it. That is the whole requirement.</p>

  <p>I ended up with a system called <code>.agent-bridge</code>. Before writing this I scanned <code>~/Source</code> to get the real numbers rather than trust my memory of them. On 6 September 2026 it was installed in <strong>twelve</strong> repositories, holding <strong>176 conversations</strong> between them.</p>

  <p>Twelve copies, all different. The two most developed had a full tool suite:</p>

<pre>actionable.py       bridge.py           bridge-watch.sh
build.sh            codex-log.sh        jira.py
prehandoff.py       review.sh           review-status.sh
session-banner.sh</pre>

  <p>The other ten had three files or fewer. I would fix a bug in one copy and never port it. I would add a feature in another and forget which repos had it. The tool for keeping agents in sync could not keep itself in sync.</p>

  <p>One thing every single copy had, all twelve of them, was a <code>registry/</code> directory: the list of which agents existed. I will come back to that.</p>

  <p>Here is the part that stung. I did not notice how bad it was, because every individual piece was reasonable. A registry is reasonable. You need to know which agents exist, right? A status field is reasonable. How else do you know if something is in review? A watcher is reasonable. Somebody has to notice when a reply lands.</p>

  <p>Every one of those is a small, sensible yes. Stack enough of them and you have built an orchestrator, and now you are maintaining an orchestrator instead of shipping the thing you wanted to ship.</p>

  <h2>The line that fixed it</h2>

  <p>I threw it away and started from one sentence:</p>

  <p class="rule-stmt">The board never knows which agents exist.</p>

  <p>No registry. No presence. No heartbeats. No scheduler. If you want to know what an agent is working on, you look at which directory the file is in.</p>

  <p>That is the whole design. Assignment is the column.</p>

<pre>.agent-board/
    todo/
        005-max-id-attempts-5-can-starve.md
    doing/
        001-comment-body-can-spoof-a-comment-header.md
    review/
        011-bare-cr-bypasses-body-escaping-and-forges-messages.md
    blocked/
    done/
    threads/</pre>

  <p>Directories are columns. Each ticket is one markdown file. Moving a ticket is a rename, and the board never runs git itself; git records the move afterwards like any other file change, so history is just <code>git log</code>. There is no database, no daemon, and no state to reconcile, because the filesystem already is the state.</p>

  <p>The implementation is one 1,364-line Python file with no dependencies beyond the standard library. If you have Python 3.11 or newer on macOS or Linux you can curl it into a project and it works. It uses <code>fcntl</code> for locking, so it is POSIX only.</p>

  <figure class="wide">
    <img src="__BOARD__" alt="The agent-board web UI: a header reading 11 tickets, 0 threads, an amber WAITING strip showing one unanswered question from claude to codex, and five columns labelled todo, doing, review, blocked and done, each holding markdown-backed ticket cards.">
    <figcaption>The board. Five columns, eleven tickets, one unanswered question in the strip at the top.</figcaption>
  </figure>

  <p>That web UI owns nothing. It is a projection. Every button posts a normal file operation and a hard refresh rebuilds the page from disk. If the server dies, you have lost nothing, because there was nothing in it.</p>

  <h2>The whole loop, in eight commands</h2>

  <p>Before the interesting story, here is the boring one, which is what you will actually do all day.</p>

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

  <p>It reviews, answers, and closes:</p>

<pre>board comment 1 "Approved. Guard is correct and the test covers zero." \
    --by codex --to claude --re 1
board move 1 done</pre>

  <p>That is the entire protocol. Two flags carry it: <code>--ask</code> says a reply is expected, <code>--re 1</code> says which message this answers. Everything else in this post is an elaboration of those two.</p>

  <h2>Two agents, one real argument</h2>

  <p>Now the interesting one. This is that same loop, catching a bug in the board itself.</p>

  <p>I had just shipped threads and an inbox. 161 tests green. I asked Codex to review the diff. It came back with this:</p>

  <blockquote>
    <p><strong>[P2] Normalize carriage returns before neutralising message bodies.</strong> board.py:233-234. If a positional CLI body or POST body contains a bare CR before a header-shaped example, this scan misses it because it splits only on newline. <code>load_ticket()</code> subsequently normalizes carriage returns into newlines, turning the example into a real message.</p>
  </blockquote>

  <p>Some background on why that matters. A message on a board looks like this:</p>

  <div class="msghdr">
    ## comment — codex <span class="sep2">·</span> 2026-09-05T23:30:01Z <span class="sep2">·</span> <span class="badge">to claude</span> <span class="sep2">·</span> <span class="badge ask">ask</span> <span class="sep2">·</span> <span class="badge">commit cba4c2a</span>
    <span class="body">[P2] Normalize carriage returns before neutralising message bodies.</span>
  </div>

  <p>The header carries four optional trailers: <code>to</code>, <code>ask</code>, <code>re</code>, <code>commit</code>. And there is exactly one rule that makes the inbox work:</p>

  <p class="rule-stmt">An addressed message carrying <code>ask</code> is pending until a later message in the same file lists its number in <code>re</code>.</p>

  <p>Not "who spoke last". Not a status field. A plain message is never pending at all. Only an ask is, and only until something answers it.</p>

  <p>So if a message body could smuggle in a fake header, an attacker, or an honest agent pasting the wrong thing, could forge a message that says <code>re 1</code> and make a real request look answered. I had already escaped that. Any body line shaped like a header gets a backslash in front of it.</p>

  <p>Codex found the hole: the escape split the body on newlines, but ticket files are read in text mode, where Python turns a bare carriage return into a newline <strong>after</strong> the escape has already run. So a <code>\r</code> walks straight past the guard and becomes a line break on the next read.</p>

  <p>I reproduced it in about two minutes:</p>

<pre>after real ask  -&gt; messages=1  pending_asks=1
after CR body   -&gt; messages=3  pending_asks=0
    #1 by='claude'  re=[]   ask=True   to='codex'
    #2 by='mallory' re=[]   ask=False  to=None
    #3 by='codex'   re=[1]  ask=False  to='claude'</pre>

  <p>Message #3 is attributed to an agent that never wrote it, and it silently cleared a pending request. One pending ask became zero.</p>

  <p>The conversation that followed used exactly the loop above. I filed it, Codex asked, I answered with <code>--re</code> and asked back:</p>

<pre>board comment 011 "Confirmed and fixed in 98f93e0, but I think you rated it
too low..." --by claude --to codex --re 1 --ask --commit 98f93e0</pre>

  <p><code>--re 1</code> discharges Codex's request. <code>--ask</code> opens a new one. Both in the same message, which is what "changes requested" actually is.</p>

  <p>Codex moved it to P1:</p>

  <blockquote>
    <p>Move to <strong>P1</strong>. The trigger is narrow, but the result silently corrupts the feature's sole source of truth: a pending ask becomes answered by a message its attributed author never wrote. That makes the inbox unsafe to trust and blocks shipping.</p>
  </blockquote>

  <p>And then it caught me overstating something, which I did not enjoy and which is the best argument for this whole setup:</p>

  <blockquote>
    <p>The named test only exercises bare CR around a forged header. It does <strong>not</strong> itself cover CRLF, ordinary LF, or a trailing CR as claimed.</p>
  </blockquote>

  <p>It was right. I had claimed the regression test covered four cases. Four cases were in the throwaway script I used to reproduce the bug. The committed test had one. The green suite did not catch that. Codex did, by reading what I wrote against what I committed.</p>

  <p>I widened the test to 164, replied with <code>--re</code>, and the inbox went quiet:</p>

<pre>$ board inbox
(nothing pending)</pre>

  <p>"Nothing pending" is a fact about the files, not a guess about the conversation.</p>

  <h2>The waiting strip</h2>

  <p>The one screen I actually look at:</p>

  <figure>
    <img src="__WAIT__" alt="A narrow amber strip labelled WAITING, listing a single entry: ticket 001, message 1, from claude to codex, asking whether ticket 001 closes now or whether ticket 011 replaces it.">
    <figcaption>Every unanswered ask on the board, in one strip.</figcaption>
  </figure>

  <p>Every unanswered ask, across every ticket and thread. No per-agent filter, no unread counts, no notification system. If a question is open, it is on that strip until something answers it.</p>

  <p>There is no notifier, by the way. The board never tells anyone anything. If you post a question and you know where the other agent is running, you nudge it yourself. The nudge carries no content, because the message is already in the file. That one choice deleted an entire subsystem from the old bridge.</p>

  <h2>The receipts</h2>

  <p>The reason this stayed small is not discipline. It is that I wrote down every feature I refused to build, and why, in a file called <code>decisions.md</code>. Condensed from that table:</p>

  <div class="tablewrap">
    <table>
      <thead><tr><th>Rejected</th><th>Why</th></tr></thead>
      <tbody>
        <tr><td>Copying a folder into each project</td><td>The predecessor was copied into many repos and diverged.</td></tr>
        <tr><td>An agent registry</td><td>Roster state goes stale. A crashed agent stays registered forever. You never need to know who exists, only what is unclaimed.</td></tr>
        <tr><td>A scheduler</td><td>Assignment is the column, or a human. An LLM scheduler is the main reason multi-agent boards get expensive.</td></tr>
        <tr><td>Leases / claim expiry</td><td>A human is in the loop and notices a stuck ticket.</td></tr>
        <tr><td>JSON tickets</td><td><code>comments[]</code> is written concurrently. JSON arrays conflict in git every time.</td></tr>
        <tr><td>SQLite</td><td>A binary blob kills <code>git log</code> for a ticket. The audit trail is worth more than query power.</td></tr>
        <tr><td>A <code>status</code> field</td><td>The directory is the only truth, so nothing can drift and there is no reconcile step.</td></tr>
        <tr><td>Presence / heartbeats</td><td>Stale presence is worse than none, because it is trusted.</td></tr>
      </tbody>
    </table>
  </div>

  <p>The first four rows are not hypothetical. The old bridge had a registry in all twelve copies, a status field on every thread, JSON messages, and a copied folder per project. Every one of those was a small sensible yes at the time.</p>

  <p>The registry went stale, because nothing removes a crashed agent from a list. The status field drifted from the directory, because two places can disagree. The JSON messages conflicted in git on every concurrent write. The copied folder is why there were twelve versions instead of one.</p>

  <p>I do not have receipts for what a scheduler or leases would have cost me, because I never built those. They are on the list because they are the next four small sensible yeses, and I would like to still be able to explain this tool in one sentence a year from now.</p>

  <h2>What I would tell you if you are starting</h2>

  <p>Do not start with the orchestrator. Start with the smallest thing that makes the work visible to the next agent, and only add machinery when the absence of it actually bites you.</p>

  <p>Concretely, the three things that carried all the weight:</p>

  <ol>
    <li><strong>Assignment is location.</strong> A ticket in <code>review/</code> is in review. There is no second place that can disagree with the first.</li>
    <li><strong>One rule for "is this answered".</strong> Not who spoke last, not a flag someone has to remember to set. A later message points at an earlier one, or it does not.</li>
    <li><strong>Plain text in git.</strong> When Codex and I disagreed about severity, that argument is a diff. I can <code>git log</code> it in a year. In the bridge those arguments lived in JSON blobs I stopped being able to read, and losing them is a large part of why I rebuilt the thing so many times.</li>
  </ol>

  <p>The tool is one Python file. It has no dependencies, no server you have to keep alive, and no concept of who you are. It is coordinating real work on a client project now, and the most interesting thing it has done so far is help me find a bug in itself.</p>

  <p>That is the bar. Not "can it orchestrate". Can it get out of the way.</p>

  <p class="endnote">The board is at <a href="https://github.com/jharjadi/agent-board">github.com/jharjadi/agent-board</a>. Every command, transcript and screenshot above is from the real board. The bug is ticket 011; the fix is commit <code>98f93e0</code> and the widened test is <code>197cc83</code>. Codex reviewed this post before it went up, on the board, and found seven things wrong with it.</p>

</article>
"""

HTML = HTML.replace("__BOARD__", BOARD).replace("__WAIT__", WAIT)
OUT.write_text(HTML, encoding="utf-8")
print(f"wrote {OUT}  ({len(HTML)/1024:.0f} KB)")
