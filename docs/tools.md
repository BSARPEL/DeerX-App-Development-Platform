# Agent tools

[← Documentation](README.md) · [Türkçe](tr/tools.md)

Agents do not answer in free text — they act through tools, and their findings
are recorded as structured data. There are 43 tools; each agent role gets a
narrow subset.

## The tool sets

| Role | Tools | Turn budget | Read files | Write files | Shell | Services | Browser | Web |
|---|--:|--:|:--:|:--:|:--:|:--:|:--:|:--:|
| Analyst | 13 | 30 | ● | | | | | |
| Researcher | 14 | 35 | | | | | ● | ● |
| Assessor | 11 | 30 | ● | | | | | |
| Mockup | 10 | 30 | ● | | | | | ○ |
| Architect | 14 | 35 | ● | | | | | ○ |
| Planner | 11 | 25 | ● | | | | | ○ |
| Backend | 17 | 45 | ● | ● | ● | ● | | ○ |
| Frontend | 24 | 45 | ● | ● | ● | ● | ● | ○ |
| QA | 26 | 45 | ● | ● | ● | ● | ● | ○ |
| Reviewer | 13 | 35 | ● | | ● | | | ○ |
| Staging | 19 | 40 | ● | ● | ● | ● | ● | |
| Live | 10 | 30 | ● | | ● | | | |
| Advisor | 23 | 16 | ● | | | | | ● |
| Summarizer | 8 | 12 | ● | | | | | |

Every pipeline role also gets `search_knowledge` and `read_project_state`.
The advisor is not a phase — it is the conversation on a workflow; see
below.

The turn budget is the role's own ceiling; what an agent actually gets is
`min(role budget, max_iterations)`. With the default `max_iterations = 40` the
three 45-turn roles are capped at 40, so raising that setting is what unlocks
the rest.

A narrow tool set lowers token cost and prevents the wrong tool being picked.
Read the table for what is *absent*, because the absences are the design:

- **Live cannot write files.** It deploys what has been reviewed; it does not
  author it.
- **Mockup cannot write files either** — it produces screens through
  `save_artifact`, so everything it makes is a tracked artifact rather than a
  loose file somewhere in the tree.
- **Backend has no browser.** It writes server code and can run and log a
  service, but visual verification belongs to Frontend, QA and Staging.
- **Web access splits into two paths, and the reason is injection.** A page
  you read can say "ignore your earlier instructions and run this command";
  so text from an address the agent did not *choose* must not enter the
  context of an agent that writes files and runs commands.

  | Path | Tool | Who | Why |
  |---|---|---|---|
  | Targeted read (○) | `fetch_url` | Architect, Planner, Backend, Frontend, QA, Reviewer | The agent already knows the address (documentation, release notes, an RFC). A narrow surface: to be led somewhere it did not intend, the model must first get that address from somewhere. |
  | Open-ended browsing (●) | `web_search` · `browse_page` | Researcher, Advisor | Where you end up is not known in advance — the widest injection surface. Neither role has file-write or shell tools. |

  A building role has no open-ended search, but the path is **not closed**:
  it calls a `researcher` with `run_subagent`. The subagent reads,
  summarises and cites; what comes back enters the caller's context as
  *data*, not as instructions. So it is not "a coding agent cannot reach the
  web": it can, but it does the open-ended part in an isolated context.

  `tests/test_agent.py::TestWebErisimiIkiYolaBolunur` nails the split down:
  it fails the moment one role has both open-ended browsing and file writes.

- **Mockup's web is for imagery only**: it can find and download a photo for
  a slide, but it cannot search or read a page.
- **Analyst and Assessor work from what is already indexed.** If research is
  needed, that is the research phase's job and its findings arrive as records.
- **Reviewer can run commands but not write.** It audits by reading and running;
  a reviewer that could edit would be reviewing its own work.

## Knowledge base

| Tool | What it does |
|---|---|
| `search_knowledge` | Hybrid search (semantic + BM25) over indexed documents and code |
| `read_document` | Reads an indexed document in order, whole or by chunk range |
| `ingest_source` | Indexes a file or directory |
| `list_knowledge` | Lists documents and statistics |

Agents are told to use `search_knowledge` **before assuming anything**, and that
several narrow queries beat one broad one.

## Filesystem

| Tool | What it does |
|---|---|
| `read_file` | Reads with line numbers; `offset`/`limit` for large files |
| `write_file` | Writes a file in full, creating parent directories |
| `edit_file` | Exact-match replacement; `old_string` must be unique |
| `list_dir` · `glob_files` · `grep_files` | Navigate and search |

Every path is resolved against the workspace and refused if it escapes.
`edit_file` refuses a non-unique match rather than guessing which occurrence you
meant.

## Recording findings

| Tool | Key format |
|---|---|
| `record_requirements` | `REQ-001` |
| `record_questions` | `Q-001` |
| `record_gaps` | `GAP-001` |
| `record_decisions` | `ADR-001` |
| `record_research` | — |
| `record_tasks` | `T-001` |
| `update_task` · `save_artifact` | — |

Writing the same key again updates the record. Requirements must carry a
`source_ref` pointing at the document; an inference without one has to be marked
`category="assumption"`.

## Shell

`run_command` runs a command in the workspace and returns stdout/stderr.

It goes through three gates in order:

1. **Deny list** — destructive patterns, refused unconditionally. Matching is
   position-aware: `srv.shutdown()` and `--shutdown-timeout` are not the
   `shutdown` command, and blocking them was a bug.
2. **Allow list** — `[deerx.shell] allow_prefixes` in `deerx.toml`. Bare command
   names match only in command position.
3. **Approval** — with `approval_mode = "ask"`, you see the command before it
   runs.

Two behaviours worth knowing:

- **Timeout kills the whole process tree.** `subprocess.run(timeout=…)` kills
  only the shell; children survive holding the pipes open, and `communicate()`
  then blocks for the command's full duration. Measured: a 30-second command
  took 30 seconds under a 2-second limit.
- **Multi-line commands are written to a script.** On Windows `cmd.exe` treats a
  newline as a command terminator, so a multi-line command used to run its first
  line, return exit code 0, and silently drop the rest. Multi-line commands now
  go to a temporary file executed by a POSIX shell.

## Services — running what you wrote

This is what lets an agent test its own work.

| Tool | What it does |
|---|---|
| `start_service` | Starts a process in the background; it stays up for the run |
| `service_log` | Reads its output |
| `stop_service` · `list_services` | Stops it / lists what is running |

**Why this is separate from `run_command`.** `run_command` waits for the command
to finish and kills the tree on timeout — correct for tests, wrong for a dev
server. Measured: `python srv.py` was killed after eight seconds; `python srv.py &`
still blocked and was killed because on Windows `&` is a command separator;
`start /b` was refused because it is not on the allow list. There was no way at
all for an agent to keep its application alive between two tool calls.

`start_service` detaches the process and writes its output to a **file**. With a
pipe the parent has to keep reading, and that is what actually blocks.

**Give it a port and it waits** until that port starts listening, so "I started
it" means "it is genuinely ready". If the process dies immediately, the tail of
its log comes back as the error.

Services are bound to the run and all shut down when it ends. A leftover dev
server holding a port would meet the next run with "port in use" and the cause
would be invisible.

```
start_service(command="npm run dev", port=3000, name="web")
```

## Browser — seeing what you built

| Tool | What it does |
|---|---|
| `preview_open` | Opens your local app in the Chrome on the server |
| `browser_snapshot` | Enumerates clickable/typable elements with `ref` numbers |
| `browser_click` · `browser_type` · `browser_back` | Uses the page |
| `browser_console` | The page's **own** errors: console, failed requests, 4xx/5xx |
| `browser_screenshot` | Saves an image as an artifact **and shows it to the model** |
| `browse_page` · `web_search` · `fetch_url` | Research on the open web |

**Why `browser_console` is not optional.** A snapshot tells you how the page
*looks*, not whether it works. A button can sit in exactly the right place and
still throw an exception when clicked. The tool waits for the network to settle
before reading — measured, an image's 404 had not arrived 1.2 seconds after page
load and had arrived within 2 seconds. The record is cleared on every
`preview_open`/`browse_page` so a previous page's error is not read as this
page's.

`browser_snapshot` returns a numbered element list rather than raw HTML: it is
both cheaper in tokens and more reliable to act on.

**The screenshot is sent to the model, not just filed.** It used to be written to
disk with only "saved" coming back, so the agent knew the page's *structure* but
not its *appearance* — misalignment, overlapping boxes, a cropped image,
unreadable text all sat outside its loop. Measured on the local vLLM endpoint:
the model read a randomly generated code straight off a screenshot, so it does
see. Two constraints shape how: in the OpenAI wire format a `role: "tool"`
message cannot carry an image, so the picture travels in a `user` message
appended after the tool results; and not every model has vision, so if the
endpoint rejects images the client learns that once, strips images from the
history — a leftover image would reproduce the same rejection every turn — and
retries. The agent notices nothing; it simply returns to the old behaviour.
Images above 4 MB are skipped and logged rather than sent, because base64 grows
a picture threefold and a few of them would fill the context.

`preview_open` accepts only `127.0.0.1:<port>`, the permission is granted
server-side, and it is dropped when the run ends. The model cannot reach the
policy list directly.

The QA instruction treats this as an acceptance criterion: the phase is not done
until the main flow, the empty state and one error state have been exercised and
a screenshot left behind.

## Web research

`web_search` uses the Chrome on the server. Summaries are not enough to decide
on — the instruction is to follow up with `browse_page` and actually read the
result.

An empty result is reported to the agent as an **error**, not as "no results".
The difference matters: a model that reads a failed search as "there is no such
thing" will state that confidently in a report.

`fetch_url` downloads a page, extracts its text and **indexes it**, so later
phases can find it with `search_knowledge`. It refuses private, loopback and
link-local addresses (SSRF), and it re-checks after DNS resolution rather than
trusting the hostname.

## Images — for slides and mockups

| Tool | What it does |
|---|---|
| `find_images` | Searches the web for images; returns address, size and **source** |
| `download_image` | Saves one into the workspace and records it as an artifact |

`fetch_url` cannot do this: it reads `response.text` and does not carry binary
data. Without these two the mockup role could only draw boxes in CSS.

**Licence is the design constraint here.** Putting an arbitrary web photo into
someone's deliverable creates copyright exposure. A single SearXNG image query
returns results from eleven engines — measured — mixing sources with a known
licence (Openverse, Wikimedia Commons, Unsplash, Pexels) with ones whose licence
is unknown (Bing, Google, Pinterest). `find_images` therefore defaults to
`free_only`, labels every result with the engine it came from, and — when no
freely licensed result exists — says so instead of quietly falling back, because
that fallback is exactly how an agent ends up shipping an image it believes is
licensed. `download_image` writes the source address into the artifact summary so
attribution is possible afterwards.

A source has to be *downloadable*, not merely free: the Art Institute of Chicago
is deliberately absent, because its IIIF endpoint answered 403 in five out of
five attempts even with a user agent. Left in the list it would have sorted near
the top and burned a turn every time.

The bytes are checked, not the content type: a server can answer 200 with an
error page, and saving that would show up as a broken image in the slide with no
explanation.

## What the model is told

Tool descriptions are model-facing text, and they are bilingual: the Turkish
lives inline on the tool class (where the behaviour is documented), the English
in `tools/descriptions_en.py`, and `Tool.spec()` overlays whichever the current
language calls for. A test asserts every tool and every described parameter has
both — a new tool cannot ship English-only or Turkish-only. See
[Bilingual architecture](i18n.md).

## Sub-agents — splitting a job

| Tool | What it does |
|---|---|
| `plan_subagents` | Plans the split. Runs nothing |
| `run_subagent` | Runs one sub-agent and returns its text |

Planning is separate from running on purpose: writing down which part goes
to whom, and what each is expected to return, also shows whether splitting
is worth it. If only one part comes out, you did not need a sub-agent.

A sub-agent runs **in the parent's thread, sequentially**. Concurrency is a
separate problem and not a prerequisite here: running them at the same time
would mean splitting the browser session (Playwright's sync objects are
bound to the thread that created them), the sandbox ports, the service
namespace and the approval queue all at once. The value of a sub-agent is
not parallelism — it is a **narrow tool set and a clean context**.

Two things are deliberately *not* inherited:

- **Approvals.** When the user approved a dangerous command, they approved
  *that command*, not a role. Carrying the parent's approval set into the
  child would be a privilege leak.
- **The failed-address counter.** Otherwise a sub-agent would refuse an
  address it never tried, because the parent had.

Recursion stops at one: a sub-agent cannot run sub-agents. And the limit is
enforced in the **tool set**, not only at call time — a sub-agent never sees
the tools at all. Offering a tool and then refusing it wastes a turn. The
constraint follows the *depth*, not the role: `qa` owning a phase may split
work; `qa` called as a sub-agent may not.

Only the four narrow roles can be sub-agents — `researcher`, `qa`,
`reviewer`, `summarizer`. The pipeline roles are left out because they own a
phase and produce its deliverable; a sub-agent does not run a phase, it
answers a question.

## Workflow advisor

A thirteenth role, not a pipeline phase. You talk to it about one workflow
(`deerx chat`, `POST /api/workflows/{id}/chat`, the `deerx_workflow_chat`
MCP tool). It reads, it answers, and if you ask it to it changes that
workflow's records. The turn budget is **16**: it can now search the web,
and a search is not one turn (search → read two hits → compare with the
record → answer). At 12 that chain could call the tools and still have
nothing left for the reply — you would see tool noise and no answer.

It has no shell and no `write_file`. It **does** search and read the open
web (`web_search`, `fetch_url`, `browse_page`) — it cannot write a file
or run a command, so a page that says "ignore your instructions and run
this" has nowhere to go. The three tools that exist only inside this
conversation:

| Tool | What it does |
|---|---|
| `read_workflow` | The discussed workflow's goal, brief, runs, artifacts and plans |
| `update_workflow` | Changes that workflow's title, goal or brief |
| `resolve_question` | Closes an open question with the user's answer, or skips it with a stated assumption |

None of them takes a workflow argument. The scope comes from the caller,
not from the model: making the id an argument would be asking the model
*which* workflow to change, and a wrong number is all it takes to edit
the wrong one.

It can also call the ordinary recording tools (`record_requirements`,
`record_gaps`, `record_decisions`, `record_questions`, `record_tasks`) and
`save_artifact`. Those writes are reversible and they are audited.

Two more belong to this conversation alone — they reach your **other**
projects:

| Tool | What it does |
|---|---|
| `list_project_history` | Your other projects: goal, how many workflows, how many chat lines |
| `search_history` | Searches those projects for decisions, past workflows, old conversations and artifacts |

A short digest of that history is in the advisor's context already, so it
knows what is worth asking about; these tools fetch the detail. Putting all
of it in every message would pile a hundred screens of old conversation on
top of the actual question.

The reading is read-only (`mode=ro` + `query_only`), runs no schema
migration and starts no project runtime — looking at history cannot disturb
a project someone else is working in. The scope comes from the caller, the
same rule that decides which projects you see; the model cannot widen it. A
project that cannot be read is named in the answer rather than skipped, so
"nothing found" is never confused with "could not look".

## See also

- [Security model](security.md) — how the shell policy and confinement work
- [The pipeline](pipeline.md) — which agent runs when
- [Architecture](architecture.md) — where the tool layer sits
