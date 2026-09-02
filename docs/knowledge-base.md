# The project's own knowledge base

[← Documentation](README.md) · [Türkçe](tr/knowledge-base.md)

DeerX carries a retrieval engine. This page points it at **DeerX itself**: the
7,500 lines of documentation, the 16,000 lines of source, and the test suite —
indexed, searchable, and readable by a model.

Why bother: the answer to "why is it done this way?" is usually in a comment or
a test docstring, not in the prose. Grepping finds the word; retrieval finds the
passage.

## Build it

```bash
uv run python scripts/knowledge/build.py
```

Roughly a minute of indexing plus embedding time — with the default
`multilingual-e5-large` on CPU, a few minutes. The result lands in `.deerx-kb/`
(gitignored).

```bash
uv run python scripts/knowledge/build.py --hizli
```

`--hizli` ("fast") skips the embedding model: lexical search works fully,
semantic search is weak. Useful for a smoke test, not for real use.

Other flags: `--hedef <path>` to build somewhere else, `--force` to reindex
files that have not changed.

### What is indexed, and why

| Path | Why it earns its place |
|---|---|
| `README*.md` | The entry point |
| `docs/` | The prose, in both languages |
| `src/deerx/` | The code — its comments explain **decisions**, which is where "why" usually lives |
| `tests/` | The best documentation in the repository: every test is a real bug, and its docstring says which |
| `scripts/` | Setup, launchers, screenshot tooling |
| `examples/` | A sample specification |

The list is **explicit**, not a repo-wide crawl. A knowledge base whose contents
nobody can enumerate is one nobody can trust.

Three files are excluded by name, measured rather than guessed:

- `static/index.html` — stripped of markup it becomes word soup. It was ranking
  first for *"audit log"* with `## Audit log User Action Rows 50 200 1000
  Refresh` while `docs/security.md`, which actually explains the audit log,
  did not make the list.
- `static/i18n.js` — 1,400 lines of key/value. It resembles every query
  slightly and answers none of them.
- `docs/images/` — binary.

## Ask it

```bash
uv run python scripts/knowledge/ask.py "what does the audit log record"
```

Three steps: hybrid search (semantic + lexical, fused with RRF), the retrieved
passages assembled into one context **with their sources**, and the model told
to answer only from those.

Three rules are deliberate:

- **Only from the excerpts.** A documentation base earns its keep by showing
  where an answer came from; an answer prefixed with "as far as I know" is the
  same as never having queried it.
- **Say when it is not there.** An invented answer costs more than a wrong one:
  recognising it as wrong requires already knowing the right one.
- **Sources are listed under the answer** regardless of whether the model cited
  them, so you always know where to look.

Useful flags:

```bash
uv run python scripts/knowledge/ask.py "sandbox" --sadece-arama   # retrieval only
uv run python scripts/knowledge/ask.py "sandbox" --ayar ./demo    # model settings from there
uv run python scripts/knowledge/ask.py "sandbox" -k 12            # more passages
```

`--ayar` matters: the knowledge base does not define a model endpoint, and it
should not have to. Point it at any workspace that already has one.

## Query it without a model

The knowledge base is an ordinary DeerX workspace, so the CLI works on it:

```bash
cd .deerx-kb && uv run deerx search "how does the sandbox work"
```

`--full` prints whole chunks instead of excerpts, `--kind doc` restricts to
documentation, `--kind code` to source.

## Let an agent use it

DeerX ships an MCP server. Point it at the knowledge base and any MCP client —
Claude Code, Claude Desktop, your own agent — can search it as a tool:

```json
{
  "mcpServers": {
    "deerx-kb": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/DeerX-App-Development-Platform",
               "deerx", "mcp", "--workspace",
               "/path/to/DeerX-App-Development-Platform/.deerx-kb"]
    }
  }
}
```

The tools that matter here are `deerx_search` (hybrid search, returns passages
with citations) and `deerx_documents` (what is indexed). See [MCP server](mcp.md)
for the full list.

## Keeping it current

The index is content-addressed: rebuilding only re-reads files whose contents
changed, so running it again after editing a few files is quick.

```bash
uv run python scripts/knowledge/build.py
```

A knowledge base that has drifted from the code is worse than none — it answers
confidently with something that used to be true. Rebuild after any change you
would want an answer to reflect.

## What it is not

This is a **reference** base, not a memory: it holds what the repository says,
not what you decided in a conversation. Project state — requirements, gaps,
decisions, tasks — lives in each workspace's own `.deerx/deerx.db` and is
reached through [the MCP server](mcp.md) or the web interface.
