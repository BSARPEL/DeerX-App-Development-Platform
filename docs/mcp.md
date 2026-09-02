# MCP server

[← Documentation](README.md) · [Türkçe](tr/mcp.md)

DeerX exposes its knowledge base and pipeline over the [Model Context
Protocol](https://modelcontextprotocol.io), so another agent — Claude Code,
Cline, or anything else that speaks MCP — can use it as a tool.

## Configure it

```json
{
  "mcpServers": {
    "deerx": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/DeerX-App-Development-Platform", "deerx-mcp"],
      "env": {
        "DEERX_WORKSPACE": "/path/to/target-project",
        "ANTHROPIC_API_KEY": "sk-ant-...",
        "DEERX_APPROVAL_MODE": "auto"
      }
    }
  }
}
```

`DEERX_WORKSPACE` decides which project the server serves. Without `uv`:

```json
{ "command": "python", "args": ["-m", "deerx.mcp_server"] }
```

`.env` is read from the **workspace**, not the current directory — otherwise a
server started this way would silently ignore the project's own key.

`DEERX_APPROVAL_MODE=auto` is usually what you want here: an MCP server has no
terminal to ask on. Understand what that means before setting it — see
[Security model](security.md).

## Tools

| Tool | What it does |
|---|---|
| `deerx_ingest` | Index files or directories |
| `deerx_search` | Hybrid search over the knowledge base |
| `deerx_documents` | List indexed documents |
| `deerx_status` | Phase statuses and counts |
| `deerx_state` | Requirements, gaps, decisions, research findings |
| `deerx_tasks` | The task list |
| `deerx_next_task` | The next task whose dependencies are met |
| `deerx_update_task` | Update a task's status and result |
| `deerx_artifact` | Fetch a produced artifact |
| `deerx_run_phase` | Run a single phase |
| `deerx_questions` | Open questions |
| `deerx_answer` | Answer one |
| `deerx_skip_question` | Move on with an assumption |
| `deerx_package` | Readiness gate + delivery zip |

## Resources

| URI | |
|---|---|
| `deerx://state` | The project memory as structured data |
| `deerx://artifacts/{name}` | A single artifact |

## The question gate over MCP

When `deerx_run_phase` hits a blocking question it returns:

```json
{ "status": "needs_input", "questions": [ ... ] }
```

**The outside agent should read the questions with `deerx_questions` and pass
them to the user — not answer them itself.** The whole point of a blocking
question is that it asks for something no amount of reasoning produces. An agent
that answers on the user's behalf reintroduces exactly the wrong assumption the
gate exists to prevent, and it does so invisibly, because the recorded answer
then looks like the user's.

## Binary artifacts

`deerx_artifact` on a `.zip` returns the package's `TESLIMAT.md` report rather
than raw bytes — the same rule the web interface follows. Handing a model an
archive's bytes produces nothing useful and costs a great many tokens.

## Two agents, one workspace

Nothing stops the MCP server and a `deerx serve` from pointing at the same
workspace. They share the SQLite project memory, which handles concurrent access
— but the vector cache is invalidated across processes precisely because it once
was not: a document indexed from one process stayed invisible to semantic search
in the other.

Do not run two pipeline **runs** against one workspace at the same time. The web
runner refuses a concurrent run for this reason; the MCP server has no way to
see a run started elsewhere.

## See also

- [CLI reference](cli.md) — `deerx mcp`
- [Configuration](configuration.md) — `DEERX_WORKSPACE` and `.env` resolution
- [Security model](security.md) — what `approval_mode = "auto"` gives up
