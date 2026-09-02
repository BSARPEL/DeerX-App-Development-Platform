# Getting started

[← Documentation](README.md) · [Türkçe](tr/getting-started.md)

This page assumes nothing. If you have never used Python, `uv`, Docker or a
local model server before, follow it top to bottom and you will end up with a
working DeerX. Every step ends with a command that tells you whether it worked.

**Total time:** about 20 minutes, plus however long your model weights take to
download.

---

## 0. What you are installing, and why

DeerX is a document-driven development agent. You give it a specification; it
analyses, researches, designs, plans, writes code, tests and packages. It runs
on **your** machine and talks to **your** model.

Four pieces, only two of them mandatory:

| Piece | Required? | What it does | Skip it and… |
|---|---|---|---|
| **Python 3.11+ and `uv`** | Yes | Runs DeerX | Nothing works |
| **A model endpoint** | Yes | The actual intelligence — a local server or Anthropic | Every phase after `ingest` refuses to start |
| **SearXNG** (Docker) | Strongly recommended | Web search for the research phase | Research invents URLs instead of finding them. Measured: one run wasted 14 turns on 9 HTTP 404s and 4 non-existent domains |
| **Google Chrome** | Optional | The agent's browser tools | Mockups are not screenshotted, no live UI checks |

You do not have to install these one by one. **`deerx setup` does most of it**
— see step 4.

---

## 1. Python, uv and Git

`uv` is a Python package manager. DeerX uses it so you never have to think
about virtual environments.

**Windows** (PowerShell):

```powershell
winget install Python.Python.3.13 Git.Git; irm https://astral.sh/uv/install.ps1 | iex
```

**macOS**:

```bash
brew install python@3.13 git && curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Linux** (Debian/Ubuntu):

```bash
sudo apt install -y python3 python3-venv git && curl -LsSf https://astral.sh/uv/install.sh | sh
```

Close the terminal and open a new one, so the new `PATH` is picked up. Then
check all three:

```bash
python --version && git --version && uv --version
```

You need Python **3.11 or newer**. If `uv` is not found after reopening the
terminal, its install directory is not on your `PATH` — on Windows that is
`%USERPROFILE%\.local\bin`, elsewhere `~/.local/bin`.

---

## 2. Get the code

```bash
git clone https://github.com/BSARPEL/DeerX-App-Development-Platform.git
```

```bash
cd DeerX-App-Development-Platform && uv sync --extra all
```

`uv sync` creates `.venv/` and installs everything. The extras:

| Extra | What it adds | Leave it out and… |
|---|---|---|
| `embed` | Local embeddings via `fastembed` | A hash fallback is used — fine for a smoke test, poor for real retrieval |
| `browser` | `playwright`, for `browse_page` and the browser tools | Browser tools are unavailable |
| `dev` | `pytest`, `ruff` | You cannot run the test suite |
| `all` | All of the above | — |

Check it:

```bash
uv run deerx --help
```

---

## 3. A model endpoint

DeerX needs a model. Pick **one** of these.

### Option A — a local server (recommended, free, private)

Any OpenAI-compatible server works: **vLLM**, **Ollama**, **LM Studio**,
**llama.cpp**. A real, working vLLM example — this is the one used to develop
DeerX, on two GPUs:

```bash
docker run -d --name qwen3-vllm --gpus all -p 8008:8000 -v /path/to/weights:/models vllm/vllm-openai:latest /models --served-model-name "qwen3.8 max" --host 0.0.0.0 --port 8000 --tensor-parallel-size 2 --gpu-memory-utilization 0.92 --max-model-len 262144 --enable-prefix-caching --enable-auto-tool-choice --tool-call-parser hermes
```

Two flags decide whether DeerX works at all:

- **`--enable-auto-tool-choice` and `--tool-call-parser`.** Every agent works
  through tools. Without these the model returns prose where DeerX expects a
  tool call, and phases fail with no clear reason.
- **`--served-model-name`.** Whatever you write here must match `model_lead`
  and `model_worker` in `deerx.toml`, character for character.

The simplest possible alternative, if you just want to see DeerX run:

```bash
ollama serve
```

then `ollama pull qwen3:8b` and use `http://127.0.0.1:11434/v1` as the endpoint
with `model_lead = "qwen3:8b"`.

### Option B — Anthropic

No server to run. Set `provider = "anthropic"` in `deerx.toml` and put
`ANTHROPIC_API_KEY=sk-ant-...` in the workspace's `.env`.

See [Model providers](providers.md) for what differs between them.

---

## 4. Create a workspace and let `setup` do the rest

A **workspace** is one project: a directory with a `deerx.toml`, a `.env`, a
`docs/` folder for your specification, and a `.deerx/` folder DeerX manages.
Workspaces are independent — own database, own settings, own server.

```bash
uv run deerx setup ~/projects/my-project
```

This one command:

| Step | What it does |
|---|---|
| Workspace | Creates the directory, `deerx.toml`, `.env`, `docs/` |
| Dependencies | Installs any missing extras |
| Docker | Reports whether Docker is available |
| **SearXNG** | Starts a private search container and **points the workspace at it** |
| Browser | Finds your installed Chrome |
| Model endpoint | Probes your endpoint and checks the model name is served |
| Embedding model | Optionally downloads the embedding model (`--with-embedding-model`) |

It prints a table with `✓` (already fine), `+` (installed just now), `!`
(warning, DeerX still runs) and `✗` (blocked). Only `✗` stops you.

> **Why SearXNG matters.** Public search engines block automated browsers —
> measured: Bing aborts the connection, DuckDuckGo and Startpage return
> CAPTCHAs, Brave suspends. A private SearXNG instance has none of that. When
> search does not work the research agent cannot *find* URLs, so it guesses
> them, and every guess burns a turn.

Now point the workspace at your model. Edit `~/projects/my-project/deerx.toml`:

```toml
[deerx]
provider = "openai"                              # any OpenAI-compatible endpoint
openai_base_url = "http://127.0.0.1:8008/v1"
model_lead = "qwen3.8 max"                       # EXACTLY as the server serves it
model_worker = "qwen3.8 max"
```

If your endpoint needs a key, put it in `.env` (never in `deerx.toml`, which
you might commit):

```bash
# ~/projects/my-project/.env
OPENAI_API_KEY=...
```

---

## 5. Check before you run

```bash
cd ~/projects/my-project && uv run deerx doctor
```

Read the table:

| Row | Green means | Red means |
|---|---|---|
| Provider / endpoint | The address is reachable | The server is not running, or the port is wrong |
| Models | The endpoint serves the names in `deerx.toml` | **Most common mistake.** `doctor` prints what it actually serves — copy that name into `deerx.toml` |
| Connection | A real request succeeded | Firewall, wrong scheme, or the model is still loading |
| Knowledge base | The index is readable | — |

A model-name mismatch caught here saves you finding out forty minutes into a
run.

---

## 6. Your first run

Put a specification under `docs/`. PDF, DOCX, Markdown, HTML and plain text are
all read. There is a sample in the repository:

```bash
cp examples/ornek-sartname.md ~/projects/my-project/docs/
```

Then:

```bash
uv run deerx run --goal "B2B field service management"
```

The default range is `ingest → plan`: it indexes your documents, analyses,
researches, assesses gaps and risks, produces mockups and an architecture, and
writes a development plan. **No code is written in that range** — read the plan
first.

To have the code written too:

```bash
uv run deerx run --to review
```

`--to live` goes all the way through packaging, staging and deployment.

### The brief

`--brief` is an instruction you write to the analyst: what to watch for, what
is not negotiable. The specification says *what* to build; the brief says *how
to approach it*.

```bash
uv run deerx run --goal "..." --brief @instruction.md
```

---

## 7. The web interface

Everything above also works in a browser — and the browser is better for
watching a run.

```bash
uv run deerx serve
```

Opens `http://localhost:8791`. Or use the management scripts, which keep the
PID and log in the workspace and can stop and restart cleanly:

```bash
./scripts/deerx.sh start
```

```powershell
scripts\deerx.cmd start
```

On Windows you can also **double-click `scripts\start.cmd`** — that one starts
the server and keeps the window open so you can read what happened.
(Double-clicking `deerx.cmd` shows help and closes; it is the command-line
wrapper.)

### Accounts

Authentication turns on **the moment one user exists**. With no users a local
install works as it always did, but **a server with no users cannot be
exposed**: `--host 0.0.0.0` refuses to start.

Create the first administrator:

```bash
./scripts/deerx.sh passwd
```

```powershell
scripts\deerx.cmd passwd
```

Or double-click `scripts\passwd.cmd`. It asks for the password twice. **While
you type, nothing appears on screen — not even asterisks.** That is normal.

### Defaults for this machine

Typing `-H 0.0.0.0 -w /srv/project` every time gets old. Copy the example:

```bash
cp scripts/deerx.local.conf.example scripts/deerx.local.conf
```

```ini
PORT=8791
HOST=0.0.0.0
WORKSPACE=/srv/projects/customer-x
```

Both scripts read it, the command line still wins, and the file is gitignored —
the repository's own default stays `127.0.0.1` so cloning it never puts anyone
on the network.

---

## 8. When it stops and asks

If an agent hits something only you can know, it records a **blocking question**
and the pipeline stops before the next phase:

```
? gate 2 unanswered questions stopped the pipeline
┌─ Your answer is needed to continue ────────────────────────────┐
│ Q-001  Can you provide the ERP system's API documentation?     │
│    Why: The integration cannot be designed.                    │
└────────────────────────────────────────────────────────────────┘
```

```bash
uv run deerx answer Q-001 "Yes, there is a REST API; OAuth2, 60 req/min limit."
uv run deerx answer Q-001 --from-file long-answer.md
uv run deerx skip Q-001 -a "Assume REST + OAuth2"
```

Your answer is written to the project memory **and** indexed into the knowledge
base, so later phases find it with `search_knowledge` even after the
conversation history has been trimmed.

Exit codes are made for scripting: `0` fine, `1` failed, `2` your answer is
awaited.

---

## 9. What you get

```
.deerx/
├── deerx.db          requirements, gaps, decisions, tasks, questions, artifacts
├── events.jsonl      the event stream, recoverable across restarts
├── artifacts/        analiz-raporu.md, mimari.md, mockup-*.html, screenshots
└── teslimat/         delivery zips
```

The web interface shows the same things: **Artifacts** grouped by run (each
carrying the workflow it belongs to), **Workflows** step by step with duration
and cost, and **Live feed** with every tool call.

---

## 10. When something goes wrong

- **[Troubleshooting](troubleshooting.md)** — symptom, cause, fix, for the
  failures that actually happen.
- `uv run deerx doctor` — the first thing to run, always.
- `.deerx/events.jsonl` — every tool call and error, in order.
- On the Settings screen, the **audit log** shows who did what and when, and
  which sign-ins were refused.

## Next

- [The pipeline](pipeline.md) — what each of the 13 phases does
- [Configuration](configuration.md) — every setting and where it can be set
- [Model providers](providers.md) — vLLM flags and what differs between providers
- [Security](security.md) — including `execution = "docker"`, which runs the
  agent's commands in a disposable container instead of on your machine
- [The project's own knowledge base](knowledge-base.md) — ask questions about
  DeerX itself, answered from its documentation and source
