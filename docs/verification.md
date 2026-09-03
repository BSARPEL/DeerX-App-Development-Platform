# Verification status

[← Documentation](README.md) · [Türkçe](tr/verification.md)

This page distinguishes what was verified **by running it** from what was not.
The distinction is the point: a claim that something works because the code
looks right is not a verification, and mixing the two makes the honest claims
worthless too.

## The suite

**1760 tests pass**, `ruff` clean, on Python 3.11 and 3.13.

No test makes a network call or a real model call. Agents run against a fake
client in `tests/conftest.py`, so the suite is deterministic.

Timing depends on the machine, and the number that used to stand here did not
say which one. Measured on a Windows 11 laptop: `--fast` (no process-spawning
tests) about 160 seconds, the whole suite 215-285. On Linux it is considerably
quicker — process spawning is most of the cost and Windows is slowest at it. Run
`--fast` while working and the whole thing before pushing.

## Verified by running

**RAG end to end.** A real PDF (text extraction through `pypdf`), a real DOCX
(`python-docx`), HTML, Markdown, Turkish characters and the cp1254 fallback.

**Hybrid search.** RRF fusion and MMR diversification, measured on Turkish
queries with `multilingual-e5-large`.

**The agent loop.** Tool dispatch, `tool_result` shape, parallel tool calls,
`pause_turn`, the iteration limit, cancellation, error propagation.

**Lane routing.** That backend/frontend/qa/infra tasks reach the right agent.

**The question gate, end to end.** A blocking question stops the run, the phase
never executes, the gate opens after an answer or a skip, and the answer is
searchable in the knowledge base afterwards. Verified through all three
surfaces — CLI, web API and MCP.

**Step selection.** Steps chosen out of order are arranged into pipeline order,
duplicates collapse, step 1 cannot be removed, an unknown step is refused.

**Interface integrity.** Every `#id` the JS looks for exists in the HTML, every
`data-view` target has a section, every CSS class used in HTML or JS is defined.
These are what silently break when a view is moved.

**Design scale.** All 1458 rendered text elements pass WCAG AA; the palette,
type scale and spacing grid are pinned.

**Broken-file resilience.** An invalid PDF or DOCX does not bring indexing down;
the error is reported and sound files still index.

**File upload.** Path traversal blocked, unsupported extensions refused, an
unreadable file not left in the workspace — and if it overwrote a working file
of the same name, the old one is restored.

**Delivery packaging.** The readiness gate blocks on an empty plan, a
failed/unfinished task and a blocking question. In a real workspace, `.env`,
`deploy.pem`, `node_modules/` and `.git/` are excluded while `.env.example` is
kept, and no secret value appears in the raw bytes of the produced zip.

**Web.** The whole HTTP API, the SSE publisher loop, that the approval gate
really blocks the run thread and releases it on an answer, refusal of a
concurrent run.

**A complete `ingest → plan` run against a local vLLM** (`qwen3.8 max`, 262K
window), on the sample field-service specification. Seven phases, **82 model
calls, 3.4M input / 329K output tokens, free**, and every phase honoured its
contract:

| Phase | Produced |
|---|---|
| `ingest` | 11 chunks, real 1024-dim `multilingual-e5-large` vectors |
| `analyze` | 35 requirements, 13 gaps, 5 questions, `analiz-raporu.md` in exactly the section structure its prompt prescribes |
| `research` | 16 findings, 12 with source URLs, each tagged with a confidence level — real Chrome navigation, with page timeouts and a 404 absorbed rather than fatal |
| `assess` | gaps 13 → 27 (1 critical, 7 high), `bosluk-analizi.md` |
| `mockup` | 6 single-file screens, one per actor in the spec, all with JavaScript and all carrying empty and error states |
| `design` | 18 ADRs — every one with rationale, alternatives **and** trade-offs — plus a 34 KB `mimari.md` whose choices cite the research phase's findings by version |
| `plan` | 42 tasks over five lanes (backend 21, qa 8, frontend 7, infra 5, docs 1); 42/42 with an acceptance criterion and named files, 41/42 with dependencies |

Two behaviours were observed live rather than inferred: the turn-budget warning
fired at exactly 70% (`24/35`), and the readiness gate refused to package,
naming the empty plan, the open critical gaps and the phases not yet run.

**The interface, rendered.** Every view, both languages, both themes, a live
model call from the settings screen (`qwen3.8 max · 2.3s · 64 → 43 tokens`),
indexing and hybrid search from the browser, a generated mockup rendering inside
its sandboxed frame, and the readiness gate. This closes what the next section
used to list as unverified.

**The container image, end to end.** `docker build` from the repository's
`Dockerfile`, then the documented sequence: `deerx user add` against a mounted
workspace, the server started on `0.0.0.0`, and `GET /` answering 200 with
`{"configured": true, "required": true}` — so the account created in the first
container survived the volume into the second. The refusal path was exercised
too: with no account, binding `0.0.0.0` stops the server rather than exposing
it.

**Bilingual.** That both languages cover every key with matching placeholders;
that changing the language changes real messages (tool errors, agent hints,
phase names); that `deerx --help` follows the environment variable **in a
separate process**; that tool descriptions and their parameters switch while the
class-level schema stays unmutated; and that no user- or model-facing string is
left hardcoded anywhere in the source.

**MCP.** Tool and resource registration, plus a JSON-RPC handshake in a real
subprocess.

**Schema migration.** On a database predating the `lane` and `plan_id` columns:
opening does not crash and tasks without a plan are carried into the main one.

**Interrupted runs.** Tasks left `running` return to the queue at startup. If
they did not, neither they nor anything depending on them would be ready again
and the plan would deadlock.

**A full analyst run against a local vLLM** (`vllm/vllm-openai` in Docker,
Qwen3, tool calling enabled): 5 model calls, 57K input / 22.8K output tokens,
**free**. The analyst read the specification end to end and produced 31
requirements (each with a `§section` reference), 12 gaps (tagged with severity
and area), 3 questions for the user (each with its reasoning and a suggested
assumption), and `analiz-raporu.md`. Tool-call round trips, parallel tool calls,
streaming generation and structured recording all worked.

**Manually in the browser.** Live SSE with a real indexing run, answering from
the question panel, drag-and-drop upload, markdown and mockup rendering, hybrid
search, the plan and analysis views, keyboard navigation.

**A development task, end to end, against the same local vLLM.** One task —
"write a `/health` endpoint with `http.server` and a pytest for it" — went from
`pending` to `done`: the agent checked the Python and pytest versions, wrote
`saglik.py` and `test_saglik.py`, and ran the tests. Its own suite passed 7/7.
Verified independently afterwards: importing the module does not start the
server (0.01 s), `GET /health` returns 200 with `application/json` and
`{"status": "ok"}`, every other path returns 404.

**[The project's own knowledge base](knowledge-base.md), queried through the
same model.** 154 documents, 1,712 chunks, 23 minutes to embed with
`multilingual-e5-large` on CPU. Three questions:

| Question | Result |
|---|---|
| *"what does the audit log record, and why are setting values not written?"* | Correct and cited, synthesised across `security.md`, `web-ui.md`, `test_auth.py` and `auth.py` — including the reason: the values contain API keys |
| *"why must `deerx.ps1` be saved with a UTF-8 BOM?"* | Correct, from a test docstring — PowerShell 5.1 reads it as cp1254, the mangled em dash opens a string and swallows the file |
| *"how is DeerX scaled on Kubernetes, which Helm chart?"* | **"This is not in the knowledge base."** Nothing invented |

The third is the one that matters. An invented answer costs more than a wrong
one: recognising it as wrong requires already knowing the right one.

## Not verified

**A live Claude API call.** There was no `ANTHROPIC_API_KEY` in the development
environment, so the real request path in `llm/anthropic_client.py` — adaptive
thinking, prompt caching — has not been exercised against the model. The
contract is covered by tests; the other side of the contract is the API itself.

**Phases 8–13 against a real model.** `implement`, `qa`, `review`, `package`,
`staging` and `live` have run only against the fake client. Phases 1–7 have now
run end to end on a real local model (above), and one implementation task was
verified separately, but nothing here reports a `plan → live` stretch: no agent
has written code, run its own tests and had the result reviewed in one
continuous run on this specification.

This is the honest remainder. The half that is verified is the half that decides
*what* to build; the half that is not is the half that builds it.

## Known fixes

Places that behaved quietly wrong, found and fixed.
`tests/test_regressions.py` guards each one — quietly wrong is the category that
needs a permanent guard, because nothing about it announces itself.

| Problem | Effect |
|---|---|
| `.env` read from the current directory, not the workspace | The documented MCP setup silently ignored the API key |
| The shell timeout did not kill the process tree | A 30-second command took 30 seconds under a 2-second limit |
| `hidden` was overridden by CSS | The approval dialog covered the whole interface at startup |
| No turn budget for parallel tool calls | 10 tools × 24K = 240K characters overflowed the context in one turn |
| An answer starting with `@` was read as a file path | `deerx answer Q-001 "@company.com…"` crashed |
| A typo in `deerx.toml` was swallowed | `aproval_mode` meant the setting never applied, with no warning |
| The vector cache went stale across processes | A document indexed from the CLI was invisible to search in the web server |
| The event log grew without bound | Long runs bloated the disk |
| A broken PDF/DOCX crashed indexing | One bad file in `docs/` brought down the whole phase |
| A truncated response looked like a finished one | The agent believed it had written the artifact; the phase produced nothing |
| A multi-line command half-ran on Windows | `cmd.exe` treats a newline as a terminator: exit 0, the rest dropped |
| A phase could report `done` with no deliverable | Later phases built on something that did not exist |
| The deny list matched substrings | `srv.shutdown()` and `--shutdown-timeout` were refused as the `shutdown` command |
| Malformed tool-call arguments entered the history | Re-read every turn, confusing the model further |
| `enable_web = false` also disabled local preview | An agent could not open the application it had just written |
| The i18n scanner ignored list literals | The setup-token banner stayed Turkish in an English install |
| `.githooks/pre-push` was documented in five places but never existed | The stated replacement for CI ran nothing; git skips a missing hook without a word |
| A record was dropped without killing the process tree | `alive` only watches the direct child, so a dead intermediate shell orphaned the real server — 115 of them had accumulated in one workspace, each holding a port |
| The setup probe called `Embedder.encode`, which does not exist | `setup --with-embedding-model` never downloaded anything; the `AttributeError` was swallowed and shown as a download failure |
| The Anthropic client ignored `ToolOutcome.images` | On `provider = "anthropic"` the model never saw a screenshot — only the text "saved". The headline capability was absent on the provider whose models all have vision |
| The input estimate counted base64 image bytes as text | A 1 MB screenshot was estimated at 559,816 tokens against a real cost of ~1,600; on a 262K window the agent's *first* screenshot killed the run with `context_overflow` |
| Images were never trimmed from history | Neither trimmer touched them, so every screenshot was re-sent every turn for the rest of the run |
| A newline was not a command separator in the shell policy | Only the first line of a multi-line command was checked, and bash ran them all: `whoami`, refused on its own, ran when placed after an allowed line. With `approval_mode = "auto"` the allow list was the only barrier |
| The proxy re-resolved the hostname after checking it | The validated addresses were discarded and `create_connection` resolved the name again — the exact second lookup DNS rebinding exploits |
| A DOCX paragraph with no style aborted the whole file | `para.style` can be `None`; one style-less paragraph meant the entire specification failed to index |

## Reproducing

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check src tests
```

For the numbers in this document:

```bash
uv run pytest -q --collect-only | tail -1        # test count
uv run deerx doctor                              # environment
```
