# Configuration

[← Documentation](README.md) · [Türkçe](tr/configuration.md)

## Precedence

```
defaults  <  deerx.toml  <  environment variables (DEERX_*)  <  CLI flags
```

`deerx init` writes a `deerx.toml` with every option documented inline. Secrets
belong in `.env` next to it, never in the TOML.

**Unknown keys are not swallowed.** A typo like `aproval_mode` produces a
warning naming the file, the key and the closest real key — a setting that
silently never applied was a bug worth never repeating.

## `[deerx]` — core

| Key | Default | What it does |
|---|---|---|
| `provider` | `"openai"` | `openai` (any OpenAI-compatible endpoint) or `anthropic` |
| `openai_base_url` | `http://127.0.0.1:8008/v1` | The endpoint. In Docker use the **host** port. |
| `model_lead` | `"qwen3.8 max"` | analyze, design, plan, qa, review, live |
| `model_worker` | `"qwen3.8 max"` | research, mockup, backend, frontend, staging |
| `model_fast` | `"qwen3.8 max"` | short auxiliary calls |
| `effort_lead` · `effort_worker` | `"high"` | Anthropic only; local models ignore them |
| `temperature` | *(endpoint default)* | Leave unset to use the server's own |
| `request_timeout_seconds` | `1800` | A local model can take minutes for one answer |
| `context_window` | *(auto)* | Override when the endpoint does not report `max_model_len` |
| `max_tokens` | `8000` | Output ceiling per turn |
| `max_iterations` | `40` | Turn ceiling per agent, capped against the role budget |
| `language` | `"tr"` | `tr` or `en` — see [Bilingual architecture](i18n.md) |
| `approval_mode` | `"ask"` | `auto`, `ask` or `dry-run` |
| `enable_web` | `true` | Internet access for research |
| `search_provider` | `"browser"` | `browser`, `duckduckgo`, `searxng`, `google`, `brave`, `tavily` |
| `searxng_url` | `"http://127.0.0.1:8890"` | Your own instance; `deerx setup` can install it |
| `google_cse_id` | — | Programmable Search engine id (`cx`); Google also needs `search_api_key` |
| `cost_limit_usd` | `0` | `0` = unlimited; the run stops when exceeded |
| `log_level` | `"INFO"` | |

### `max_tokens` and the timeout

These two interact. A local reasoning model produces roughly 70 tokens/second,
so `max_tokens = 32000` makes a single turn take about seven minutes. If
`request_timeout_seconds` is smaller than that, requests get cut mid-generation
and the failure looks like a model problem.

DeerX checks the pair at startup and warns when they contradict each other.

The default of `8000` leaves room for thinking while keeping a turn to a
reasonable length. Raise it if the model's thinking is being cut off — the agent
will tell you, because a truncated response is detected and reported rather than
mistaken for a finished one.

### `approval_mode`

| Value | Behaviour |
|---|---|
| `ask` *(default)* | Every file write and command is shown to you first |
| `auto` | Nothing is asked — for automation and unattended runs |
| `dry-run` | Writes are reported, not applied |

`auto` is what `deerx run --yes` sets for one run.

## `[deerx.rag]` — knowledge base

| Key | Default | Notes |
|---|---|---|
| `embedding_model` | `intfloat/multilingual-e5-large` | dim 1024, ~2.2 GB |
| `embedding_dim` | `1024` | **Must match the model** |
| `embedding_provider` | `"fastembed"` | `"hash"` = offline testing, poor retrieval |
| `chunk_tokens` | `700` | |
| `chunk_overlap_tokens` | `100` | |
| `top_k` | `8` | Results per search |

Smaller alternatives:

| Model | dim | Size |
|---|--:|---|
| `sentence-transformers/paraphrase-multilingual-mpnet-base-v2` | 768 | ~1.0 GB |
| `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | 384 | ~0.2 GB |

**Changing the model means re-indexing.** Update `embedding_dim` at the same
time and run `deerx ingest --force`. If you forget, DeerX refuses to search
rather than returning silently wrong results — vectors of different dimensions
cannot be compared, and a quiet empty result set is worse than an error.

## `[deerx.shell]` — command policy

| Key | Default |
|---|---|
| `enabled` | `true` |
| `timeout_seconds` | `300` |
| `allow_prefixes` | `git`, `python`, `uv`, `pip`, `pytest`, `ruff`, `mypy`, `node`, `npm`, `npx`, `pnpm`, `yarn`, `tsc`, `jest`, `vitest`, `ls`, `cat`, `head`, `tail`, `grep`, `find`, `wc`, `echo`, `mkdir`, `docker`, `make`, `go`, `cargo` |

An empty `allow_prefixes = []` means only the deny list applies — every command
not explicitly destructive is permitted. Read [Security model](security.md)
before doing that.

## `[deerx]` — isolated execution

All of these are also in the web interface, under **Settings → Isolation**;
changes there apply to the session and rebuild the container. Write them here to
persist.

By default the agent's `run_command` and `start_service` run **on this machine**,
fenced by the shell allow-list. Set `execution = "docker"` and both run inside a
disposable container instead; the allow-list is then not applied, because there
is no host to protect and the container is deleted when the run ends.

| Key | Default | Notes |
|---|---|---|
| `execution` | `"host"` | `host` or `docker` |
| `sandbox_image` | `"python:3.13"` | Not `-slim`: measured, slim has no `git`, `curl`, `gcc` or `make` |
| `sandbox_setup` | `""` | Runs once when the container is created, e.g. `apt-get update && apt-get install -y nodejs` |
| `sandbox_port_base` | `8100` | First published port |
| `sandbox_port_count` | `10` | How many are published |
| `sandbox_memory` | `"2g"` | |
| `sandbox_cpus` | `2.0` | |
| `sandbox_pids` | `512` | |

Ports are published to `127.0.0.1` when the container is created, so a service
must pick one from that range and bind `0.0.0.0` inside. Docker cannot add
published ports later. The resource caps are not decoration: without them a fork
bomb or a memory hog would not stay in the container.

See [Security](security.md) for what this does and does not isolate — in
particular, the workspace is mounted, so the machine is protected but the
project is not.

## Environment variables

Every `[deerx]` key can be set as `DEERX_<KEY>` in upper case. Keys read from
`.env` in the workspace:

| Variable | For |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic |
| `OPENAI_API_KEY` | OpenAI-compatible endpoints that require a key |
| `DEERX_OPENAI_BASE_URL` | The endpoint address |
| `SEARCH_API_KEY` | Brave or Tavily |
| `DEERX_WORKSPACE` | Which workspace to use, from any directory |
| `DEERX_LANGUAGE` | `tr` or `en`, for one invocation |

**`.env` is read from the workspace, not the current directory.** Otherwise
`deerx serve --workspace X`, or an MCP server started with `DEERX_WORKSPACE`,
would silently ignore the project's own key. If both exist, the workspace wins.

### Pinning one workspace

Workspace resolution walks **upwards** looking for a `deerx.toml`, so a command
run from a parent directory never finds a workspace nested below it. To make one
workspace the answer regardless of where you are:

```bash
export DEERX_WORKSPACE=/srv/projects/customer-x
```

```powershell
setx DEERX_WORKSPACE D:\projects\customer-x
```

An explicit `--workspace` still wins — a flag is a more specific intent than an
environment. A value that is not a directory is **not** accepted silently: it
warns and falls back to the current directory, because a typo would otherwise
mean your commands quietly ran somewhere else.

The management scripts have their own equivalent,
[`scripts/deerx.local.conf`](cli.md), which pins the port and address too.

### `DEERX_LANGUAGE` overrides the file

The language is the one setting where an environment variable beats
`deerx.toml`. CLI help text is built at import time, before any config is read,
so it can only follow the environment variable — and if the file won at runtime
you would get English help with Turkish output. See
[Bilingual architecture](i18n.md).

```bash
DEERX_LANGUAGE=en uv run deerx status
```

## Overriding prompts

Create `prompts/<role>.md` in your workspace to replace the packaged instruction
for that role without touching code. Lookup order:

```
workspace/prompts/<role>.md   →   package prompts/<language>/<role>.md   →   package prompts/<role>.md
```

Roles: `analyst`, `researcher`, `assessor`, `mockup`, `architect`, `planner`,
`backend`, `frontend`, `qa`, `reviewer`, `staging`, `live`, and `_shared` which
is prepended to all of them.

## Settings in the web interface

The Settings screen edits most of these live. Two things to know:

- **API keys never come back.** Reading settings returns only whether a key is
  set, never its value.
- **Changes are for the session.** For them to persist, write them to
  `deerx.toml`. A model setting cannot be changed while a run is in progress,
  and changing one drops the LLM client — otherwise the change would quietly do
  nothing until the server restarted, because the client reads those values at
  construction. The isolation settings behave the same way and rebuild the
  container: Docker fixes published ports and resource limits when the container
  is created.

## See also

- [Model providers](providers.md) — endpoint setup and what differs
- [Security model](security.md) — what the shell policy actually guarantees
- [Web interface](web-ui.md) — the Settings screen
