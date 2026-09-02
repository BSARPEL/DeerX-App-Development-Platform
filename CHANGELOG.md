# Changelog

All notable changes to this project are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0]

First public release. Pre-1.0: the API and the configuration format may still
change between minor versions.

### Added

**The pipeline.** Thirteen phases from `ingest` to `live`, with twelve role
agents. Findings are recorded as structured data (requirements, gaps, decisions,
research notes, tasks, artifacts) in a SQLite project memory, and each phase
inherits the previous one's records.

**The question gate.** Agents distinguish a gap the team can resolve from
information only the user has. A blocking question stops the pipeline *before*
the next phase starts, and the answer is written to both the project memory and
the knowledge base so later phases can still find it after history trimming.

**Agents that run what they write.** `start_service` keeps a dev server alive
across tool calls; `preview_open` and the browser tools open it in a real
Chrome; `browser_console` surfaces the page's own errors. The QA instruction
treats using the application as an acceptance criterion.

**Provider-independent model layer.** Any OpenAI-compatible endpoint (vLLM,
Ollama, LM Studio, llama.cpp, OpenAI) or the Anthropic API. Context windows are
discovered from the endpoint and requests clamped to fit.

**Hybrid retrieval.** Local ONNX embeddings, SQLite + FTS5, RRF fusion and MMR
diversification — no external vector database.

**Three surfaces.** A web interface (no build step), a CLI, and an MCP server.

**Delivery packaging.** A readiness gate that refuses to package unfinished
work, secret exclusion with every excluded file named in the manifest, and a
`TESLIMAT.md` that reports what was left out.

**Authentication.** `scrypt` with per-user salts, server-side revocable
sessions, account lockout, and a refusal to bind a non-loopback address with no
users configured.

**Full bilingual support (Turkish / English).** One setting reaches the
interface, the CLI, the event stream, tool errors, and the agent instructions
and tool descriptions the model itself reads. A test scans the source and fails
if any user- or model-facing string bypasses the message catalog.

### Fixed

Behaviours that were silently wrong before release. Each is guarded by a test in
`tests/test_regressions.py`:

- A response truncated at the token ceiling was indistinguishable from a
  finished one — the agent believed it had written an artifact that did not
  exist. Truncation is now detected and the agent is told to continue rather
  than restart.
- A phase could report `done` without producing its deliverable, so later phases
  were built on nothing. The orchestrator now checks, nudges once, then fails.
- Multi-line shell commands ran only their first line on Windows and returned
  exit code 0. They are now written to a script and run by a POSIX shell.
- The shell timeout killed only the shell, not its children — a 30-second
  command took 30 seconds under a 2-second limit.
- The deny list matched substrings, refusing `srv.shutdown()` and
  `--shutdown-timeout` as the `shutdown` command.
- Malformed tool-call arguments entered the conversation history and were
  re-read every turn.
- `.env` was read from the current directory rather than the workspace, so the
  documented MCP setup silently ignored the project's key.
- A typo in `deerx.toml` was swallowed; unknown keys now warn with a suggestion.
- The vector cache went stale across processes, hiding CLI-indexed documents
  from the web server's search.
- `enable_web = false` also disabled local preview, so an agent could not open
  the application it had just written.
- An answer beginning with `@` was treated as a file path.
- A broken PDF or DOCX brought down the whole indexing phase.
- The event log grew without bound.
- The i18n scanner did not descend into list literals, leaving the setup-token
  banner in Turkish on an English install.

[Unreleased]: https://github.com/BSARPEL/DeerX-App-Development-Platform/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/BSARPEL/DeerX-App-Development-Platform/releases/tag/v0.1.0
