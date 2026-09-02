# Model providers

[← Documentation](README.md) · [Türkçe](tr/providers.md)

DeerX works with two providers. **The default is a local OpenAI-compatible
endpoint** — zero token cost, and the documents never leave the machine.

| Provider | Covers | Required setting |
|---|---|---|
| `openai` *(default)* | vLLM, Ollama, LM Studio, llama.cpp, OpenAI | `openai_base_url` |
| `anthropic` | Claude API | `ANTHROPIC_API_KEY` |

## Tool calling is mandatory

All of DeerX is built on tools. A model that cannot call tools cannot run a
single phase — it will produce prose where the pipeline expects records.

For vLLM that means `--enable-auto-tool-choice` and a `--tool-call-parser`
matching your model:

```bash
docker run --gpus all -p 8008:8000 \
  vllm/vllm-openai:latest /models/local \
  --served-model-name "qwen3-coder-30b" \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --max-model-len 262144
```

Then point DeerX at it:

```toml
# deerx.toml
[deerx]
provider = "openai"
model_lead = "qwen3-coder-30b"
model_worker = "qwen3-coder-30b"
```

```bash
# .env
DEERX_OPENAI_BASE_URL=http://127.0.0.1:8008/v1
OPENAI_API_KEY=...          # only if your endpoint requires one
```

Verify before you start a long run:

```bash
uv run deerx doctor
```

`doctor` lists the models the endpoint actually serves and says so when the name
in your config is not among them. A model-name mismatch is the most common
setup mistake and otherwise surfaces forty minutes in.

## Context window handling

A local endpoint will reject a request whose `max_tokens` plus prompt exceeds
its window, with a 400 in the middle of a long run.

DeerX discovers the window itself: it reads `max_model_len` from the endpoint's
model list, estimates the input size, and clamps the requested output so the
request fits. If the endpoint still refuses, the numbers in its own error are
used to retry once.

If discovery is not possible, set it explicitly:

```toml
[deerx]
context_window = 262144
max_tokens = 64000
```

There is also a consistency check: if `max_tokens` at a typical local generation
rate would take longer than `request_timeout_seconds`, DeerX warns at startup
rather than letting requests be cut mid-generation.

## What actually differs between the two

### Server-side web search — Anthropic only

On Anthropic, `web_search` and `web_fetch` run on Anthropic's infrastructure.

Everywhere else, search is the hardest part to get right, and the honest
answer is measured rather than assumed.

#### What was measured

With DeerX's honest User-Agent (`DeerXAgent/0.1`), on a real Chrome:

| Engine | Result |
|---|---|
| Bing | **Serves a decoy result set to automation** — a 200 and plausible HTML for a completely different topic |
| DuckDuckGo (html / lite) | CAPTCHA — "select all squares containing a duck" |
| DuckDuckGo (JS) | Page loads, results never render |
| Startpage · Mojeek · Brave · Ecosia | Access Denied · 403 · Captcha · challenge |
| Google | Redirects to a consent page |
| Public SearXNG instances | 429 / 403 (rate limited) |

The Bing behaviour is the dangerous one. Asked about
`BaseHTTPRequestHandler threading`, it returned Domino's Pizza Japan and
Google Photos — with a 200 and normal-looking markup. A failed search is
reported to the agent as an error it must not treat as an answer; a *decoy*
search looks like research and ends up cited in a report.

DeerX now detects this: if not one result contains any term from the query,
the set is discarded and the "the search did not work" error is raised
instead.

#### The fix that works: your own SearXNG

```bash
docker run -d --name deerx-searxng --restart unless-stopped \n  -p 127.0.0.1:8890:8080 \n  -v /path/to/searxng:/etc/searxng:rw \n  searxng/searxng:latest
```

In `settings.yml`, add `json` to `search.formats` — it is **off by default**
and the endpoint returns 403 without it:

```yaml
use_default_settings: true
server:
  secret_key: "change-me"
  limiter: false
search:
  formats: [html, json]
```

Then:

```toml
[deerx]
search_provider = "searxng"
searxng_url = "http://127.0.0.1:8890"
```

No key, no blocking, no rate limit — it is your instance. And SearXNG is
honest about coverage: its `unresponsive_engines` field says which engines
failed and why, and DeerX passes that through to the agent rather than
letting coverage narrow silently.

Measured on the same queries that broke Bing:

```
os.replace atomic file write windows
  1. os.link() vs os.rename() vs os.replace() for writing atomic write files
     stackoverflow.com/questions/60369291
  2. Extending os.rename() to support file swapping and whiteout
     discuss.python.org/t/22257
```

#### Google, through the official endpoint

Google's search page refuses an automated browser outright — measured, real
Chrome and all: *"Our systems have detected unusual traffic from your computer
network."* Working around a bot check is not something this project does, so
Google comes only through its licensed Programmable Search JSON API:

```toml
[deerx]
search_provider = "google"
google_cse_id = "..."          # from programmablesearchengine.google.com
```

```bash
# .env
SEARCH_API_KEY=...             # Custom Search API key
```

It needs **both**; with only one, the endpoint answers 400 and the message tells
you nothing, so DeerX names the missing setting instead. The free tier allows
100 queries a day, and a research phase uses a handful.

If you already run SearXNG you may not need this: SearXNG can query Google
server-side from your own instance, with no key and no quota.

#### The keyed alternative

If you would rather not run a container, `brave` or `tavily` with a key are
licensed for programmatic access and are not blocked:

```toml
[deerx]
search_provider = "brave"      # or "tavily"
```

```bash
# .env
SEARCH_API_KEY=...
```

These can also be set from the Settings screen, and there is a **Test search**
button that actually searches.

An empty result is reported to the agent as an **error**, never as "no results".
A model that reads a failed search as "there is no such thing" writes that into
a report as fact.

`fetch_url` reads a known address without any key and works on both providers.
`browse_page` (for JavaScript-rendered pages) needs the `browser` extra.

### Prompt caching — Anthropic only

Anthropic's prompt cache covers the system prefix. DeerX keeps the system prompt
**fixed** and puts variable project state into the first user message, precisely
so the prefix stays cacheable — variable content in the system prompt would
invalidate the cache every turn.

vLLM's `--enable-prefix-caching` gives a similar benefit from the same design.

### Adaptive thinking — Anthropic only

`effort` and adaptive thinking are Anthropic parameters. Local models ignore
them; a reasoning model like qwen3 thinks through its own parser instead.

### Streaming and retries

The OpenAI-compatible client streams and assembles tool calls as they arrive. It
also repairs one failure mode that used to poison a run: a malformed tool-call
argument blob. Rather than appending it to the history — where it would be
re-read every turn and confuse the model further — the client validates the JSON
first and drops what does not parse.

Transient stream failures are retried twice before the error propagates.

## Roles and models

Three model slots, mapped to roles:

```toml
[deerx]
model_lead   = "claude-opus-5"     # analyze, design, plan, qa, review, live
model_worker = "claude-sonnet-5"   # research, mockup, backend, frontend, staging
model_fast   = "claude-haiku-4-5"  # short auxiliary calls
```

With a single local model, set all three to the same name.

## Cost

Local models are priced at zero. Claude is priced from a table in
`llm/pricing.py`, and every phase records what it spent.

```toml
[deerx]
cost_limit_usd = 5.0    # 0 = unlimited
```

When the ceiling is passed the run stops with `BudgetExceeded` rather than
continuing to spend. The message names the current total and the limit.

## Truncation

A response cut off at the output ceiling used to be indistinguishable from a
finished one — the agent believed it had written the artifact, and the phase
ended having produced nothing.

The client now checks `stop_reason`. On `max_tokens` the agent is told its
message was cut off, that nothing it was writing was saved, and to continue from
where it stopped rather than starting over. After two truncations in a row it
gives up with a message naming `max_tokens` as the thing to raise.

## See also

- [Configuration](configuration.md) — every setting and its precedence
- [Getting started](getting-started.md) — first run
- [Architecture](architecture.md) — how the provider layer is isolated
