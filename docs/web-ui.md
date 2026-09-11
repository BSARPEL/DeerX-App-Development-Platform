# Web interface

[← Documentation](README.md) · [Türkçe](tr/web-ui.md)

```bash
uv run deerx serve
```

Opens `http://localhost:8791`. No build step — the interface is plain
`index.html` + `styles.css` + `app.js`, served with `no-cache` so a stale
`app.js` cannot outlive an API change.

> The server can write files and run shell commands. It binds `127.0.0.1` by
> default. See [Security model](security.md) before changing that.

## The top bar

Cost, run status, a **TR / EN** language switch and the theme toggle.

The language switch changes everything in one click — and it **persists to the
server**, because only half of what you read comes from the browser. The event
stream, tool errors and the agent instructions come from the Python side; a
switch that changed only the client would leave the interface in English and the
stream in Turkish. If the server refuses the change, the interface reverts with
it rather than sitting in a language the server does not share.

The same setting is still on the Settings screen, and both routes go through one
function — otherwise one of them would persist and the other would not, and the
difference would be invisible.

On narrow screens the cost is dropped from the bar; it is already a card on the
Overview.

## The left rail

The sections, and at the bottom the three things that are true no matter which
screen you are on: the approval mode, the two models in use, and **which
workspace you are in** — its folder name.

That last one is there because two DeerX windows on the same machine look
identical. The path was a row on the Settings screen, which meant checking it
required leaving the screen you were about to press **Start** on.

Only the folder name is printed. The name is what tells two workspaces apart;
the full path took two lines and put a home directory into every screenshot of
the interface. It is on the button's tooltip, and clicking copies it — if the
clipboard is unavailable the path comes back as a toast, so the button is never
silently dead.

On narrow screens the whole rail foot is dropped along with the rail's vertical
layout; the workspace is still on the Settings screen.

## Overview

![The overview: the pipeline has stopped at a question only the user can answer](images/overview-en.png)

The summary dashboard. **Questions waiting for an answer** sit at the top —
above everything else, because a blocked pipeline is the only thing that needs
you right now.

Below: the 13-phase pipeline strip (which agent, which status, what it cost),
count cards, a phase summary with each agent's own closing note (newest first),
and recent events.

## Develop

![Develop: the document list on the left, the phase picker and the brief on the right](images/develop-en.png)

Where the work starts. Two steps side by side.

**1 · Give a document.** Drag the specification in. The file lands under
`docs/`, is indexed immediately, and the documents the model can actually read
are listed right underneath — so "did it get my spec?" is answered on the same
screen.

**2 · Start a run.** You pick the steps from a list, grouped into four stages
(**Understand · Design · Build · Deliver**). Clicking a stage heading selects or
clears the whole group.

Each row says what the step will *produce* rather than what it is called —
"A task list split into lanes, with dependencies" rather than "plan". A list of
phase names is not information if you do not already know the pipeline.

Rows also show which agent will run, a badge if the step is already done, and
its cost. The presets `All` / `Analysis` / `Code` come ready, and the path the
run will follow is summarised on one line below the list.

A start from this screen creates a **workflow** and a **run** inside it. The
difference — and why a retry is a new run of the same workflow — is in
[Concepts](concepts.md#workflows-runs-plans-and-tasks).

Two behaviours worth knowing:

- The selection is arranged in **pipeline order** regardless of the order you
  clicked.
- **Step 1 (ingest) is always included.** With an empty knowledge base no later
  agent has anything to read.

## Runs

![A workflow broken into runs, each with status, duration and cost](images/workflow-en.png)

Every run is persistent under its own id and gets a sequential number
(`#1`, `#2`, …).

The list shows goal, steps, how many finished, duration, cost, date and status.
Opening a run gives a step-by-step breakdown — each step on its own card: which
agent ran, how many tool calls and model responses, how many errors and
warnings, how long, what it cost, what it produced. Expanding a card reveals the
agent's closing summary, the error text, the artifacts (clickable) and **that
step's raw event stream**.

Running steps and steps with problems come open by themselves.

**Steps come from the run's own record**, not from the phase state. Phase state
belongs to the project and is overwritten on every re-run — reading it while
looking at a past run would show you today's result under yesterday's heading.
Events are tagged with the run and phase that produced them, and the breakdown
survives a server restart through `.deerx/events.jsonl`.

### Resuming after a failure

A failed step carries a **Re-run from here** button, and the workflow view shows
**Resume from the failed step** next to the error. Either one starts a fresh run
that begins at that step and continues through the rest of *that run's* steps.

Three things make this faithful rather than approximate:

* **The earliest failure is chosen**, not the last. Later failures are usually
  consequences of the first one; starting behind them walks into the same wall.
* **The step list comes from the original run**, not from the pipeline order. If
  you ran `ingest → analyze → plan`, the retry will not quietly add `research`
  and `assess` — you left those out on purpose.
* **The run remembers what it was running.** A run started for a single task
  carries that task key, so the retry runs that task and not every task that
  happens to be ready.

Steps are forced rather than skipped: you asked for this one explicitly, and
"already done" would make the button do nothing. The steps after it are forced
too, because output built on a step that failed is suspect.

A step waiting on an answer (`needs_input`) is not offered a retry. The agent
did its work and is waiting on you; re-running it only asks the same question
twice. Answer it in **Overview** instead.

The retry is recorded in the audit log as `run.retry`.

## Talking about a workflow

A workflow's detail view has **Talk about this workflow**. It opens a drawer
scoped to *that* workflow — the floating button says the same thing.

You ask, or you tell it what to change. The advisor can close a question,
rename the workflow, edit the goal or the brief, and record a finding. It
cannot run a command or write a project file. Changes it does make are listed
under the reply, so a silent edit cannot hide.

The conversation is stored with the workflow. **Delete history** clears it.
The same conversation is `deerx chat` on the CLI and `deerx_workflow_chat` on
MCP; there is one history, not three.

See [Concepts — The advisor](concepts.md#the-advisor) for what it is allowed
to touch, and why the workflow id is not an argument the model gets to pick.

## Live stream

![The live feed: every tool call and model step, filterable by type](images/stream-en.png)

Every step an agent takes arrives over SSE: tool calls, model text, cost,
errors. Filterable by type and paginated — the last page is "live" and new
events flow there.

Going back to an earlier page does not make the stream slide out from under you.
The counter keeps rising and a **● Back to live** button appears.

**It opens with history, not empty.** The tail of `.deerx/events.jsonl` is read
back on load, so a restarted server does not erase what the agent did — the
overview's *Recent events* fills in too. The stream had claimed to be persisted
while showing nothing after a refresh; auditability that stops at the screen is
not auditability.

**Scope: the whole project, or one workflow.** The selector at the top narrows
the feed to a single workflow. The filter runs on the server
(`/api/events/history?workflow=`), not in the client: history is read from the
end, and a third workflow's events can sit far behind the last 400 lines —
filtering client-side would never see them. If the whole log was not scanned
the screen says so; "older entries not scanned" and "no events" are not the
same claim.

Each row now also prints the event's **phase**. The field was in the record all
along; the screen simply never drew it.

**Search** runs over the message and the actor, client-side.

**The connection is on screen.** A line appears when the feed drops and clears
when it comes back. It used to reconnect silently: nothing on screen, and a
stalled feed looked like "no events".

## Plan

![The task plan: lanes, dependencies and per-task status](images/plan-en.png)

**Multiple plans.** A plan is a named, independent group of tasks: parallel
workstreams, alternative approaches, or a new version after the spec changed.
The strip at the top selects, creates, renames and deletes them. **●** marks the
*active* plan — where the planner writes new tasks.

The task list filters by the selected plan. Each task can be advanced
individually (**Implement this task**), or the whole plan run with **▶ Start** —
the button says how many tasks are ready, and why none are if that is the case.

Task keys are unique project-wide, so a task in one plan can depend on a task in
another with no ambiguity.

The plan does not have its own screen. It lives inside **Runs**, under a single
workflow — the task graph belongs next to the run that executes it, and on the
workflow *list* the question "whose tasks?" has no answer. An old `#/p/<slug>/plan`
link still lands there.

**Blocked means blocked.** The *Blocked* filter covers tasks waiting on an
unfinished dependency, not only those a run marked `blocked` — the filter meant to
show why a plan is not moving was the one thing not showing it. A row says what it
is waiting on, and a dependency that no longer exists is marked apart from one that
is merely unfinished: "T-014 is gone" and "T-014 is pending" are different facts.

Deleting a plan strips its keys from the dependency lists of tasks in other plans,
and says how many it touched. Left behind, those keys were never going to be
`done`, so the tasks waiting on them would never be ready — silently, forever.

## Analysis

![Analysis: requirements, questions, gaps, decisions and research](images/analysis-en.png)

Requirements, gaps, architectural decisions and research findings. Clicking a
row opens its evidence and recommendation. Paginated (25/50/100/250); switching
tabs returns to page one, and open detail rows do not bleed across pages.

**Filters and search.** Each tab filters on its own categorical fields:
priority and category for requirements, severity and area for gaps, status and
blocking for questions, confidence for research. The chips are **derived from
the data** — whatever values actually occur in that tab. Drawing a chip for a
value that is not in the data would invite a click that can never return
anything. The chips stay when a filter empties the list, or there would be no
way back.

**The counter and the table come from the same moment.** All five sections are
fetched in a single `/api/state/all` call and the tab counters are the lengths
of those lists. The counters used to come from `/api/overview` (refreshed every
2.5s) while the table came from a separate call: mid-run the screen could say
"Requirements 24" above "No requirements". A side benefit: switching tabs no
longer costs a request.

**What you typed survives.** The screen redraws every 2.5s during a run; open
detail rows and text typed into an answer box are carried across.

**Open questions are answerable here.** The Questions tab is not a read-only
log: expanding an open question gives you the answer box, and the answer goes
into the knowledge base like any other. Only *blocking* questions used to be
answerable, and only during the halt — a question the pipeline had walked past
could never be answered, which sits badly with a product whose first claim is
that it asks instead of guessing.

## Knowledge base

What the model can actually search.

**Documents.** The inventory: what is indexed, of what kind, how many chunks.
Search over name and path, chips for kind and state. The header line says how
many documents are **active** — the number the agents can actually see. The
total is the inventory; telling someone who deactivated three documents that
they still have "27 documents" would be quoting a number they cannot use.

**Deactivate really removes it.** "Stop looking at this" narrows both the
search box and the corpus the agents read; the chunks stay, so undoing it does
not need a re-index. A run's document scope cannot **undo** it: a scope is a
narrowing ("in this run, look only at these"), deactivation is an exclusion
("in no run, look at this").

**Permanent delete stops at the workspace edge.** The index entry always goes;
the file is only unlinked when it lives inside the workspace. The corpus can
carry outside paths (`deerx index <dir>` will index anywhere), and deleting a
file in someone else's directory is not the project's to do. When the file is
left behind, the feed says so.

**Search.** A query plus kind chips — document, code, web, data. The same
hybrid search as `deerx search`: semantic plus BM25, fused by rank. Results
name the source and the chunk, so "did it index my spec?" is answered here
rather than forty minutes into a run.

This box is a **diagnostic**: hits from deactivated documents come back too,
dimmed and labelled "agents do not read this document". The answer to "why
isn't this found?" is usually "because you deactivated it", and zero results
never says that. The agent's own search never opens this door.

**Indexing.** A path, an optional **Force**, and the list of documents already
in the index, paginated. Uploading on **Develop** lands the file under `docs/`
and indexes it; this screen is for everything else — an extra folder, a
re-index after you changed the embedding model, a check that a fetched page
stayed.

Unchanged files are skipped unless you force them. Changing `embedding_model`
without `--force` (or this checkbox) leaves old vectors next to a new
dimension, and DeerX refuses to search rather than return silently wrong
hits.

### Adding a document without the file dialog

The drop zone opens the operating system's file dialog, and that dialog is not
ours: a network drive, a cloud shell extension, or the window opening behind the
browser can lock it, and then there is no other way in from the interface.
**Index the docs/ folder** is the second door — it indexes what is already in
`<workspace>/docs/` without writing anything. Most uploads went to that folder
anyway, so the dialog was often copying a file back to where it already was.
Unchanged files are skipped, so pressing it twice costs nothing.

## Artifacts

![Artifacts: a generated mockup rendered live inside a sandboxed frame](images/artifacts-en.png)

**Grouped by run, collapsible.** Each row is a run: **the workflow it belongs
to**, its own number, its goal, how many attachments (🗜) and how many
artifacts. Opening it lists everything that run produced, each saying which
phase made it. The newest run comes open and the rest closed — with twenty runs,
all-open means you cannot find the one you came for.

A run is a *step of a workflow*, so "which workflow did this mockup come from?"
used to have no answer on this screen — you had to go to Workflows and hunt. The
`WF #n` badge answers it, and clicking it takes you to that workflow's steps.
Runs recorded before workflows existed carry no badge rather than an invented
number.

- Markdown reports are rendered; raw HTML injection is disabled.
- HTML mockups render live inside a `sandbox` iframe.
- **Screenshots are shown, not offered for download.** `browser_screenshot`
  says the user sees the image in the interface; while `.png` counted as opaque
  binary that was not true. Raster images (`png`, `jpg`, `gif`, `webp`, `avif`)
  render inline. `.svg` deliberately does not: SVG can carry script, and opened
  directly it would run in the application's own origin.
- Zip and other binary artifacts sit as **attachments** with a download card.
  Dumping an archive's bytes as text produces a screen of garbage; instead the
  package's `TESLIMAT.md` is rendered underneath as a report.

Delivery packages appear under their own runs, not duplicated at the top.
Manual packaging creates a single-step run record — otherwise the package it
produced would belong to no run at all.

Artifacts from before run records existed are listed too, in their own group
under *Produced before run tracking*. They used to be hidden behind a button;
the badge said 11 while the screen showed 1, and a number that reads differently
in two places is wrong even when there is a control to reconcile it.

**Everything is downloadable, from anywhere.** Each row carries a download
link, each run header downloads that run's artifacts as one zip, and
**Download all** in the page header bundles the project. Reports and mockups
used to be readable but not obtainable — the only way to get a markdown report
was to copy it out of the *Source* tab.

The bytes come from the database, not the folder. An artifact whose file was
deleted from `.deerx/artifacts/` still downloads; one that exists only on disk
(an older record) says *on disk only*; one that is in neither says *file
missing* and offers no link — a button that leads nowhere is worse than no
button.

**All my projects** switches the same screen to a cross-project tree: project →
run → artifact, with a download on every stored row. Clicking a name switches
to that project and opens the artifact there; the context change is shown, not
hidden — the project name in the left rail changes with it. An artifact with no stored copy says *not in the database* and offers no link:
the cross-project endpoint never touches another project's disk, so it cannot
promise that opening the project would help.

The same view carries the **delivery panel**: readiness status, a package
button, zip downloads and a **Report** button per package.

## Environment

Per project: which container the commands run in, which ports are published,
and which services the agent has started.

**The badge separates three things that used to look alike.** Running, not yet
built, and *cannot* be built were all the same amber "not installed" — and the
third one is a fault, not a state. Host mode is now its own word (nominal, grey)
because there is no sandbox to be missing.

When the sandbox cannot be built the screen says **what happened and what to
do**: Docker Desktop cannot bind the workspace → quit it completely, start it
again, then *Check health*. The cause used to surface only on the run's first
tool call, as a line of raw Docker stderr in the middle of the event feed.

*Check health* runs a **deep** probe: it actually starts a container, mounts the
workspace and looks for `python`, `node`, `npm` and `git`. Opening the screen
only runs the cheap probe (does Docker answer, is the image present, what is the
container's state) — a deep probe on every visit would hang for a minute on
exactly the fault it exists to diagnose.

The port range is shown in both modes but labelled honestly: outside a
container nothing enforces it.

## Settings

![Settings: the isolation panel, with the agent's commands set to run in a container](images/settings-en.png)

One screen, **three scope tabs**, three Save buttons — because "let me look at
my own setting" and "let us manage the users" are a tab apart, not a navigation
apart:

| Tab | Holds | Who may write |
|---|---|---|
| This project | models, generation limits, run behaviour | project `developer` |
| My account | language, your password, your open sessions | you |
| Platform | provider and keys, isolation, web research, browser, log level, users, audit log | account `admin` |

A field's tab comes from the server (`field_scopes`), never from a list copied
into the interface — a copied list drifts from the field table the day a setting
is added, and that setting then appears in no tab at all. Each Save sends only
its own tab's unlocked fields. One button sending all thirty-eight meant that a
member changing nothing but their language still had `sandbox_image` in the
body, the request was refused at the first platform field, and they could save
*nothing*.

"My account" really is per-person: it is written to
`<DEERX_HOME>/users/<id>.toml`, not into the project file. While every scope
landed in two buckets, switching the interface to English switched it for
everyone who opened that project.

**Isolation** is where `execution` lives — host or Docker container — with the
image, the setup command, the published port range and the memory/CPU/process
limits. It was configurable only by hand-editing `deerx.toml`, even though
running isolated is one of the three things the README leads with. Choosing
*host* hides the container fields rather than showing settings nothing reads.

Three buttons make real calls:

- **Test the connection** tells the model to write "OK" and reports the
  duration, token count and answer.
- **Test search** actually searches.
- **Test the browser** actually opens one.

The difference between these buttons and discovering "the model name was wrong"
forty minutes into a run is the reason they exist.

Each panel header carries a status line derived from the settings — whether a
key is set, which search provider is in use, whether the browser runs headless
and may open the agent's own app. The search line names the provider's licence
situation rather than assuming a key is required: three of the six providers
(`browser`, `duckduckgo`, `searxng`) need none, and a fresh install used to open
with a red "search will not work" warning next to a search that worked.

Three rules:

- **API keys never come back** — only whether they are set.
- **A model setting cannot change mid-run**, and changing one drops the LLM
  client. The client reads those values at construction, so without the drop the
  change would quietly do nothing until a restart.
- **Isolation cannot change mid-run either**, and changing it rebuilds the
  container. Docker fixes published ports and resource limits at creation time,
  so nothing less than a rebuild would take effect.
- **Changes are session-scoped.** Write them to `deerx.toml` to persist.

## The approval gate

With `approval_mode = "ask"`, every file write and command execution is shown in
the browser with its preview, and the run thread blocks until you answer.

That blocking is real, not cosmetic: a test verifies the run thread is actually
held and released by the answer.

## Projects

A **project is a registered directory**. Its identity is a row in the platform
database; its data stays in its own file, `<project>/.deerx/deerx.db`. The path
is a property of the row, not the project itself.

The alternative — one central database with a `project_id` on every table — was
rejected for three measured reasons. It would force the `UNIQUE` constraints on
`requirements.key`, `tasks.key`, `artifacts.name`, `documents.source` and
`phase_state(phase)` into composites, which in SQLite means recreating five
tables and copying data: the repository's first migration that moves data and
corrupts a project if interrupted. The test fixtures assume one `ProjectState`
and one `KnowledgeBase`, and under the directory model that signature never
changes. And the agent's environment is *already* a directory — the sandbox
mounts it, services run in it, file tools are confined to it — so merging the
data would leave two different isolation axes.

Authorization has **two layers**, and mixing them is the mistake to avoid:

| Layer | Roles | Governs |
|---|---|---|
| Account | `admin`, `user` | Platform matters: credentials, isolation, who may create accounts |
| Project | `owner`, `developer`, `viewer` | Work inside one project |

A platform administrator reaches every project — otherwise a project whose owner
left would become a directory nobody can open. A project owner cannot touch
platform settings. A `viewer` reads; a `developer` runs; only an `owner` hands
out membership. The member list itself is readable by every member: it is not a
secret, and without it the name in "ayse started this run" belongs to nobody.

**Disable, don't hide.** A write control you lack the role for stays where it
is, greyed and inert, with a title saying which role it needs — deleting it
would be the lie "there is no such thing", and you would keep looking for it.
Each control declares its requirement in `data-needs-role`, and a test reads the
server's own `_require_role` calls and refuses any control that claims a
different role than the endpoint it posts to. Only a *section* whose entire
content is out of reach is hidden: a locked "Add user" form fills the screen
with noise and tells you nothing.

The three refusals no longer look alike. A **403** becomes a screen with a way
out, a **404** says the record is gone, a **409** says who is busy; only an
unreachable server is a badge, plus a strip saying how old what you see is.
While all three were the same grey "Could not load", there was no way to know
whether to ask for access, go back to the list, or wait.

A run remembers **who started it** (`runs.started_by`; empty is legitimate — a
run from `deerx run` has no owner). Your own name is never printed back at you,
someone else's always is, and stopping someone else's run asks first. The
approval modal opens only for the person who started the run and only for a
`developer`: it is full-screen and blocking, and every browser attached to the
project used to get it — a viewer got locked into a dialog whose both buttons
returned 403.

Registering a project does not create or touch the directory: the record is a
label placed on something that already exists. The same directory cannot be
registered twice — two rows pointing at one directory would mean two projects
sharing a database, each seeing the other's tasks. Archiving hides a project
without deleting it; deleting one would delete the history of everything done
in it.

**The address carries the project**: `#/p/<slug>/<view>[/<detail>]`. That is
what makes a link shareable — which project "look at this plan" opens is decided
by the sender, not by the recipient's cookie. The hash reaches the server as an
`X-DeerX-Project` header, read *before* the cookie; the cookie survives only as
"the project I used last", for an address with no hash. A cookie is
browser-wide, so while it was the only carrier, switching project in tab A moved
tab B's next request too — and *Start* in B ran a different project.

It costs nothing in safety: membership is verified on **every** request, header
and cookie alike, since both come from the client. An unverified slug resolves
to no project rather than opening someone else's work.

Each open project gets its own runtime: settings, event log, orchestrator and run
manager. That is what makes `RunBusy` project-scoped — before, one person's run
refused everyone else's, which is the opposite of multi-user. At most eight
projects stay open at once; when the limit is reached the least recently used
**idle** one is closed. A project with a run in flight is never closed: closing
it would cut someone's work in half.

## Users and authentication

Authentication is active **as soon as one user exists**. A local install with no
users works as it always did — but **a server with no users cannot be exposed**:
`--host 0.0.0.0` refuses to start. Printing a warning would not be enough for an
endpoint that writes files and runs commands.

The first administrator is created with a **setup token** printed only to the
server's console, so whoever reaches the server first cannot claim the admin
account.

Administrators manage accounts from the interface, including **disable**.
Disabling is not deleting — someone who left may return, and deleting their
account would make their traces in the history meaningless. A disabled account's
sessions drop immediately; otherwise disabling would do nothing until they
signed out.

See [Security model](security.md) for the password policy and the decisions
behind it.

## The audit log

Below the account panels, and **only for administrators**: who signed in when,
what they ran, what they changed. Every row carries a time, a name, an action, a
detail and the address it came from; refused sign-in attempts are in there too,
in red and under the name that was tried.

Three filters — person, kind of action, number of rows. The lists that fill them
come from the log itself, not from the user list: a deleted account's rows are
still searchable, and a name that was only ever *attempted* is offered as well.
They also do not narrow each other. Picking "Runs" leaves the person list whole,
because a filter that shrinks the other filter makes the second choice
impossible.

Action names are stored as fixed identifiers and translated at render time. The
run titles carry a translation key too, which is why a run started in Turkish
still reads correctly on an English screen — the same lesson the run list
learned the hard way.

The log is capped at the last 5000 rows and shares the project database.

## Design

**The neutrals are grey.** Every one of the thirteen neutral tokens used to sit
in the same blue family as the logo (LCh hue 265–272, chroma 4–13 in light,
6–21 in dark), so the brand navy was one more blue among blue greys. Chroma is
now ≤ 4 with lightness kept, which means no contrast ratio moved — ratios depend
only on luminance. The hue stays at 265: a cool grey, not a warm one, so it does
not fight the logo.

**Colour goes to marks, not prose.** Status lives in a 6px dot, a 3px stripe or
a tonal badge; sentences are ink. The one red sentence is an error message.
`--warn` is reserved for states that wait on a person (needs input, approval,
blocked, offline). The feed used to colour nine kinds of message; now only the
glyph is coloured.

**Four surface steps, each with one meaning.** Chrome (rail and top bar) recedes
from the paper by at least ΔL* 4.5; paper is the content, panels, tables and
inputs; the well (`--surface-2`) is for table heads, secondary buttons and code;
the raised surface is only for what actually floats — modal, drawer, toast,
sign-in card — and elevation is told by shadow, never by a border. A box either
carries a fill or a hairline, not both. Hairlines come in two tones: section
rules are `--border-strong`, row separators are `--border`.

**Two control heights** (36px and 28px) replaced ten. **One badge geometry** —
tonal fill, no border, rectangle — replaced seventeen pill classes; a pill means
clickable, a rectangle means a label. Status badges carry a dot as well as a
colour. Nominal values (category, area, kind) are cell text, not badges.

**Type**: six sizes (11/12/15/19/24/30), two weights, three line-height tokens,
letter-spacing only at 24px and above. Monospace is reserved for what cannot be
reflowed — code, paths, keys, identifiers and the timestamp column; the event
feed, counters and numbers are set in the body face with tabular figures. On
Windows the stack prefers *Segoe UI Variable Text* and *Consolas*; no font is
downloaded.

None of this is by eye. The palette is locked in `tests/test_web.py::TestPalette`
and `tests/test_theme.py`; the layer above it — line-height and control-height
tokens, neutral chroma, chrome/paper separation, the two dark blocks staying in
sync, mono reserved for identity, raised surfaces, no shadow on in-flow boxes,
the chromatic-text budget, one badge geometry — is locked in
`tests/test_design_system.py`.

Light and dark themes, full keyboard navigation, mobile layout: below 820px the
rail becomes two rows of five, all ten items visible without scrolling.

## Interface integrity

A set of tests checks that every `#id` the JS looks for exists in the HTML, that
every `data-view` target has a section, and that every CSS class used in the
HTML or JS is defined. Those are exactly the things that break silently when a
view is moved.
