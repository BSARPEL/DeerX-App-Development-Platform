# Troubleshooting

[← Documentation index](README.md) · [Türkçe](tr/troubleshooting.md)

## How to read this page

Every entry below is a **symptom that actually occurred**, followed by the cause
that was measured and the fix that resolved it. Nothing here is hypothetical; if
a cause was inferred rather than reproduced, the entry says so.

The entries are grouped by where the symptom appears, not by where the bug lives
— because when something breaks you know the first and not the second.

Two habits solve most of what follows before you reach this page:

- **Read the log, not the screen.** `deerx run` prints a summary; the event log
  in `<workspace>/.deerx/events.jsonl` prints the reason.
- **Test the connection before the run.** The Settings screen has three buttons
  that make real calls. Forty minutes into a pipeline is a bad time to discover
  the model name was wrong.

## The model

### Every call fails with 401 Unauthorized

**Symptom.** Every phase dies immediately with
`Error code: 401 - {'error': 'Unauthorized'}`, while the Settings screen shows
the model as ready.

**Cause, measured.** `Settings.llm_ready` treats a base URL as sufficient for a
local OpenAI-compatible endpoint, on the assumption that most local servers want
no key. That assumption is wrong for a vLLM server started with `--api-key`.
The readiness indicator was reporting the assumption, not the endpoint.

**Fix.** Set the key. In the workspace `.env`:

```bash
OPENAI_API_KEY=...
```

or enter it on the Settings screen, where it is write-only — it can be set and
replaced, never read back.

DeerX now appends the remedy to the bare 401 itself: it distinguishes "no key is
configured" from "the endpoint rejected the key you configured", because those
are different problems and the raw 401 sends you looking in the wrong place.

### The run ends early and the phase report is half-written

**Symptom.** A phase reports `done`, but its deliverable stops mid-sentence, and
the phases after it work from an incomplete input.

**Cause, measured.** The response was cut at the generation ceiling. When that
happens mid-tool-call the half-finished call is discarded and `tool_calls`
arrives empty — so a truncated response looked exactly like a response that had
finished its work, and the loop ended quietly. In one thirteen-phase run the
`assess` phase hit exactly 16000 tokens and stopped.

**Fix.** Raise `max_tokens`, and check that the context ceiling is not the real
limit:

```toml
[deerx]
max_tokens = 32000
```

The loop now detects both forms of truncation — an empty `tool_calls` with
`stop_reason = "max_tokens"`, and a tool call whose arguments failed to parse —
and tells the model it was cut off instead of letting it fall out of the loop.

### The model talks but never calls a tool

**Symptom.** The phase produces prose in the log and no artifact, then is marked
`done`.

**Cause.** Some endpoints serve a model that was never trained for tool use, or
serve it through a template that drops the tool block. The phase contract exists
precisely because "the model stopped talking" is not "the model finished".

**Fix.** Each agent phase must leave a named deliverable
(`analiz-raporu.md`, `mimari.md`, and so on — see
[The pipeline](pipeline.md)). If the file is missing the phase is not accepted.
If it is missing on every phase, the model is not calling tools at all: try a
different model on the same endpoint, and use **Test the connection** to confirm
the endpoint answers the tool-use shape.

## Configuration

### A setting in `deerx.toml` does nothing

**Symptom.** You set `search_provider = "searxng"` and `approval_mode = "auto"`,
nothing changed, and nothing warned you.

**Cause, measured.** The keys were at the top of the file, outside the `[deerx]`
table. Everything outside that table is ignored — and the unknown-key check ran
against the empty root dictionary, so even a typo produced no warning.

**Fix.** Give the file its header:

```toml
[deerx]
search_provider = "searxng"
approval_mode = "auto"
```

DeerX now says so out loud rather than swallowing it:

> Settings in deerx.toml were IGNORED: search_provider. They must live under the
> [deerx] table. Add a [deerx] line at the top of the file.

### The Settings screen opens empty

**Symptom.** Every field on the Settings tab is blank on a fresh install, as if
nothing has a default.

**Cause.** The tab rendered before the overview request that carries the values
had returned. There was no error — just an empty form, which reads as "nothing
is configured" when in fact everything is configured with defaults.

**Fix.** Fixed in the UI: the tab now waits for the overview and renders the
defaults. If you still see blanks, the overview request itself is failing —
check the browser console and the server log.

### The screen and the file disagree

**Symptom.** `deerx.toml` says one thing, the Settings screen shows another.

**Cause.** Precedence. Environment variables beat the `.env` file, which beats
`deerx.toml`, which beats the built-in defaults. A value exported in the shell
that launched the server wins over the file you are editing.

**Fix.** Decide where a setting lives and keep it there. Secrets belong in
`.env` (gitignored); everything else belongs in `deerx.toml`. See
[Configuration](configuration.md) for the full precedence table.

## Web research

### Search returns nothing at all

**Symptom.** The research phase produces notes with no sources, and the Settings
screen shows a red warning that search will not work.

**Cause, measured.** Three of the six providers — `browser`, `duckduckgo` and
`searxng` — need no API key, but the status line assumed every provider did, so
a fresh install warned about a search that worked fine.

**Fix.** The warning now reports the provider's actual licence situation. If
search genuinely returns nothing, name the provider explicitly and test it:

```toml
[deerx]
search_provider = "searxng"
searxng_url = "http://127.0.0.1:8890"
```

Then press **Test search**, which performs a real query rather than checking
configuration.

### Google answers 400

**Symptom.** `search_provider = "google"` fails with a 400 whose message
explains nothing.

**Cause.** Google's Programmable Search JSON API needs **two** values — the API
key and the search engine id — and returns an unhelpful 400 when either is
missing.

**Fix.** Set both:

```toml
[deerx]
search_provider = "google"
google_cse_id = "..."
```

```bash
# .env
SEARCH_API_KEY=...
```

DeerX names the missing one instead of passing the bare 400 through. The free
tier allows 100 queries a day; a research phase uses a handful.

### The browser provider hits a bot check

**Symptom.** `search_provider = "browser"` returns a page saying
*"Our systems have detected unusual traffic from your computer network."*

**Cause, measured.** Google's search page refuses an automated browser outright
— with real Chrome, not only with a headless one.

**Fix.** Use a provider that does not require defeating a bot check: `searxng`
(your own instance, no key, no quota — and it can query Google server-side),
`duckduckgo`, or Google through its licensed API as above. Working around bot
detection is not something this project does.

### Research invents URLs and burns its turn budget

**Symptom.** The research phase fills the event log with `fetch_url` failures —
HTTP 404s and *"could not resolve host"* on domains that do not exist — and
eventually warns that it is near its turn budget. Measured in one real run:
nine 404s, four unresolvable domains, fourteen turns spent.

**Cause, measured.** Those failures are a *consequence*, not the problem. Look
further up for a `web_search` error instead. If it says
`bing: net::ERR_ABORTED`, search is falling back to scraping a public engine and
being blocked — so the agent cannot *find* URLs and starts guessing them.

The common reason is that a working SearXNG is installed but not selected.
`deerx setup` starts the container and switches `search_provider` for you, but
it can only switch a setting that is there: a `deerx.toml` that was hand-trimmed
and has no `search_provider` line at all used to be left on `browser` silently.
Check what is actually in effect:

```bash
uv run deerx doctor
```

**Fix.** Put the setting in the `[deerx]` table — not at the end of the file,
where TOML would attach it to the last sub-table and it would do nothing:

```toml
[deerx]
search_provider = "searxng"
searxng_url = "http://127.0.0.1:8890"
```

Then confirm the instance answers JSON, which is not on by default:

```bash
curl "http://127.0.0.1:8890/search?q=test&format=json"
```

Re-running `deerx setup` also fixes it now, including when the line is missing.

## Running and testing what the agent writes

### `run_command` refuses a command

**Symptom.** The agent tries a command and the tool returns a refusal instead of
output.

**Cause.** The shell allow-list. On the host, the agent may run only what the
policy permits — this is the fence that keeps a model's mistake from becoming
your problem.

**Fix.** Either widen the policy deliberately in `deerx.toml`, or move execution
into a container where the fence is unnecessary:

```toml
[deerx]
execution = "docker"
```

Inside a container the allow-list is not applied, because there is no host to
protect and the container is deleted when the run ends. See
[Security](security.md) for what that does and does not isolate — the workspace
is mounted, so the machine is protected but the project is not.

### A service starts but the port never answers

**Symptom.** `start_service` reports the service as started, and nothing answers
on the port.

**Cause, measured.** Two separate bugs, both mine. Ports are published when the
container is created and Docker cannot add them afterwards, so a service that
picks a port outside the published range is unreachable. And the readiness check
was probing from the host, which meant it could report "ready" while nothing was
listening inside.

**Fix.** The service must choose a port from the published range and bind
`0.0.0.0` inside the container:

```toml
[deerx]
sandbox_port_base = 8100
sandbox_port_count = 10
```

`--network host` does **not** reach the Windows host; only published ports do.

### The container has no `git`, `gcc` or `node`

**Symptom.** The agent's build step fails on a missing tool.

**Cause, measured.** `python:3.13-slim` ships without `git`, `curl`, `gcc` and
`make`. The default image is deliberately **not** the slim variant.

**Fix.** Keep the default, or install what you need once at container creation:

```toml
[deerx]
sandbox_image = "python:3.13"
sandbox_setup = "apt-get update && apt-get install -y nodejs npm"
```

### `docker run` fails with "port is already allocated"

**Symptom.** With `execution = "docker"` the run dies at once:
`Bind for 127.0.0.1:8100 failed: port is already allocated`.

**Cause, measured.** The container name is derived from the workspace path, so
two workspaces get two containers — but the *published port range* is the same
`8100-8109` for both. Docker reserves published ports when the container is
created, so the second one cannot start while the first is up. The same happens
when a previous run's container was never removed.

**Fix.** Give the second workspace its own range, in **Settings → Isolation** or
in its `deerx.toml`:

```toml
[deerx]
sandbox_port_base = 8200
```

Or remove the container that is holding them: `docker rm -f <name>`, where the
name is the `deerx-sbx-…` in the error message.

## The web server

### It refuses to start on a non-loopback host

**Symptom.** `deerx serve --host 0.0.0.0` exits instead of starting.

**Cause.** Deliberate. Binding a non-loopback address with no user accounts
would publish an unauthenticated agent that can run commands and write files.

**Fix.** Create a user first, then bind:

```bash
deerx user add <name>
```

The command prompts for the password; it is never taken from the command line,
where it would land in shell history.

### Reachable here, not reachable from another machine

**Symptom.** The server answers on this machine and times out from another.

**Cause.** Three independent gates, and it is usually not the one you suspect:
the bind address, the Windows firewall, and — if the address is not on your
network — the router.

**Fix.** In order:

1. Confirm the bind address is `0.0.0.0`, not `127.0.0.1`. A loopback bind is
   invisible from everywhere else, including from a container on the same box.
2. Open the port in Windows Defender Firewall for the profile the network is
   actually using (Private vs Public matters).
3. From the other machine, test the port itself before blaming the app.

A container reaches the Windows host through `host.docker.internal`, not through
`localhost` — inside a container `localhost` is the container.

### The session is lost on every request

**Symptom.** You log in, and the next request is unauthenticated again.

**Cause, measured.** The session cookie was marked `Secure`, which means the
browser will not send it back over plain HTTP. Over HTTPS it worked; over HTTP
it silently vanished.

**Fix.** Fixed: the flag is now set from the request scheme, so it is on for
HTTPS and off for HTTP. If you are behind a proxy that terminates TLS, make sure
it forwards the scheme, or the server will see plain HTTP.

## Delivery

### The delivery gate refuses to package

**Symptom.** `deerx` will not produce a delivery archive.

**Cause.** The readiness gate. A package is a claim that the work is finished,
and the gate checks that the claim is true — required phases complete, required
deliverables present.

**Fix.** Read what the gate reported; it names what is missing rather than
failing generically. Complete the phase, or re-run it.

Separately, a matching set of secret patterns is excluded from every archive.
If a file you expected is missing from the package, check whether it looks like
a credential — that exclusion is not configurable, and deliberately so.

## Windows

### Stopping a run leaves processes behind

**Symptom.** You stop a run and a Python process keeps working.

**Cause, measured.** `CTRL_BREAK_EVENT` is delivered to the **console**, not to
a process. `CREATE_NEW_PROCESS_GROUP` separates the group but not the console,
so a break intended for a child could reach the parent — or reach nothing.

**Fix.** Use the management scripts rather than sending signals by hand. The
teardown path walks the process tree (`taskkill /F /T`), which is what actually
reaches a grandchild spawned by a build tool.

### A console window flashes on every command

**Symptom.** Black windows appear and disappear while the agent works.

**Cause.** Each child process was getting its own console.

**Fix.** Fixed: child processes are now created with `CREATE_NO_WINDOW`
alongside `CREATE_NEW_PROCESS_GROUP`. If you still see flashes, they come from a
tool that creates its own console — the flag applies to processes DeerX spawns,
not to what those processes spawn.

## Tests

### `ModuleNotFoundError: deerx`

**Symptom.** `pytest` fails to import the package; `python -m pytest` works.

**Cause.** `python -m pytest` puts the current directory on `sys.path`; the
`pytest` console script does not.

**Fix.** Use the project's own check script, which runs lint and tests the same
way in every environment:

```bash
bash scripts/check.sh
```

`--fast` skips the slow tests. On Windows, `scripts/check.ps1` is the same
script. The pre-push hook in `.githooks/` runs it, so what fails locally is what
would have failed on push.

### A documentation test fails after a code change

**Symptom.** You add a tool or a test, and `tests/test_docs.py` fails.

**Cause.** Working as intended. Several numbers in the documentation are pinned
to the code: the total tool count, the per-role tool counts, and the test count.
A hand-maintained number with eight copies cannot stay correct on its own — the
Turkish README once claimed 558 tests when there were 997.

**Fix.** Update the number the failure names. The message tells you the file,
the string it found and the value the code reports.

The same file also requires the two languages to have **identical heading
outlines**. If you add a section to one language, add it to the other.

## When none of this helps

Collect these three things before asking:

- The event log for the failing run: `<workspace>/.deerx/events.jsonl`
- The effective configuration, with secrets removed
- What you expected to happen, and what happened instead

[Verification status](verification.md) records what has been verified by running
it and what has not. If your symptom is in the "not verified" column, that is
information too — it means no one has measured this path yet.
