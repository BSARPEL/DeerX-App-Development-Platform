# CLI reference

[← Documentation](README.md) · [Türkçe](tr/cli.md)

Every command follows the interface language. `deerx --help` is available in
both; see [Bilingual architecture](i18n.md).

## Workspace

### `deerx init [path]`

Creates a workspace: `deerx.toml`, an empty `.env`, `docs/` and `.deerx/`.

| Flag | |
|---|---|
| `--force` | Overwrite an existing `deerx.toml` |

### `deerx doctor`

Checks the environment: provider, endpoint reachability, whether the configured
model is actually served, installed optional dependencies, and the knowledge
base. Run this before a long run rather than after it fails.

## Knowledge base

### `deerx ingest [paths...]`

Indexes documents and code. With no paths, indexes the workspace according to
the configured include/exclude patterns. Unchanged files are skipped.

| Flag | |
|---|---|
| `--force` | Re-process unchanged files too |

### `deerx search "query"`

Hybrid search — semantic plus BM25, fused by rank.

| Flag | |
|---|---|
| `-k N` | Number of results (default 6) |
| `--kind doc\|code\|web\|data` | Filter by source kind, repeatable |
| `--full` | Print whole chunks instead of the first 900 characters |

## Running the pipeline

### `deerx run`

Runs a phase range. Default `ingest → plan`, which produces the analysis,
research, gap assessment, mockups, architecture and plan — **no code**.

| Flag | |
|---|---|
| `--from <phase>` | Starting phase (default `ingest`) |
| `--to <phase>` | Ending phase (default `plan`) |
| `--doc <path>` | Specification file or directory to index, repeatable |
| `--goal "..."` | The user goal, passed to every agent as context |
| `--brief "..."` \| `--brief @file.md` | Free-form instruction for the analyst |
| `--force` | Re-run completed phases |
| `--yes` / `-y` | `approval_mode=auto` for this run |
| `--dry-run` | Report writes instead of applying them |

```bash
uv run deerx run --to review --goal "B2B field service management"
```

### `deerx phase <name>`

Runs a single phase. `--force`, `--yes` as above.

### `deerx implement`

Runs the implementation phase.

| Flag | |
|---|---|
| `--task T-003` | Implement only this task |
| `--yes` / `-y` | Skip approvals |

## Questions

### `deerx questions`

Lists open questions. `--all` includes answered ones.

### `deerx answer <key> "text"`

Answers a question. The answer goes to the project memory **and** the knowledge
base.

| Flag | |
|---|---|
| `--from-file` / `-f <path>` | Read the answer from a file |

`--from-file` is an explicit flag rather than an `@path` prefix, because an
answer can legitimately begin with `@` — `deerx answer Q-001 "@company.com gets
it"` used to crash.

### `deerx skip <key>`

Moves on with an assumption.

| Flag | |
|---|---|
| `--assumption` / `-a "..."` | The assumption to record; without it the agent forms its own |

## Inspecting

### `deerx status`

Phase table with status and cost, plus counts: documents, requirements, gaps,
decisions, questions, tasks, artifacts.

### `deerx tasks`

Task list with status, kind, title and dependencies. A ✓ marks tasks whose
dependencies are met.

| Flag | |
|---|---|
| `--status pending\|running\|done\|blocked\|failed` | Filter |

### `deerx artifacts [name]`

Lists produced artifacts, or renders one. Markdown is formatted.

## Delivery

### `deerx package`

Checks the readiness gate and produces the delivery zip. See
[Delivery packages](delivery.md).

| Flag | |
|---|---|
| `--force` | Package despite the gate; blockers are written into the manifest |
| `--output` / `-o <dir>` | Where to write the zip |

## Users

Authentication activates as soon as one user exists.

```bash
deerx user add sarpel --admin    # the first account is always the primary admin
deerx user list
deerx user passwd sarpel         # drops all open sessions
deerx user ensure admin          # create it, or reset its password
deerx user disable ekip          # disable without deleting
deerx user enable ekip
deerx user remove ekip --yes
```

Passwords are prompted, never taken as an argument — an argument would land in
the shell history and in `ps` output.

`ensure` covers the three states in one command: with no users it creates the
primary admin, with the account missing it adds one, otherwise it resets the
password. That is for scripts, which would otherwise have to parse `user list`
— a Rich table whose shape moves with the library version.

### Resetting a forgotten admin password

```bash
./scripts/deerx.sh passwd            # Linux, macOS
scripts\deerx.cmd passwd             # Windows
```

Double-click `scripts\passwd.cmd` on Windows for the same thing. Add
`-a name` / `-Account name` for an account other than `admin`.

The script reads the password itself, twice, with the echo turned off, and
hands it to `deerx user ensure --stdin` through a pipe. There is a reason it
does not simply call `deerx user passwd`: that command prompts through
`getpass`, which on Windows reads the console **directly** and never sees piped
input — driven from a script it hangs with no output at all.

While you type, nothing appears — not even asterisks. The scripts say so before
prompting, because a prompt that swallows keystrokes silently reads as broken.

## Servers

### `deerx serve`

Starts the web interface.

| Flag | Default |
|---|---|
| `--host` | `127.0.0.1` |
| `--port` / `-p` | `8791` |
| `--workspace` | The nearest workspace |
| `--open` / `--no-open` | Open a browser |

A non-loopback `--host` refuses to start when no users are configured.

### `deerx mcp`

Runs the MCP server over stdio. `--workspace` sets which workspace it serves.
See [MCP server](mcp.md).

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Failure |
| `2` | Waiting for your answer (the question gate), or the readiness gate blocked packaging |

The third code exists so a script can tell "this broke" from "this needs a human".

```bash
uv run deerx run --to review
case $? in
  0) echo "done" ;;
  2) uv run deerx questions ;;
  *) echo "failed" ;;
esac
```

## Management scripts

The same four commands on all three operating systems. The PID and log live in
the workspace's `.deerx/`, so each workspace manages its own server.

```bash
./scripts/deerx.sh start          # Linux, macOS
scripts\deerx.cmd start           # Windows
```

`stop` · `restart` · `status` · `logs [-f]`

Options: `-p 9000` (port), `-w ./demo` (workspace), `-H 0.0.0.0` (address).

The `.cmd` wrapper works even under a restrictive PowerShell policy — it passes
`-ExecutionPolicy Bypass` for that one call and does not touch the machine
setting. PowerShell directly also works:
`.\scripts\deerx.ps1 restart -Port 9000`.

### Defaults for one machine

Typing the same flags every time gets old, and changing the scripts' built-in
defaults is not the answer — this repository is public, and a default of
`0.0.0.0` would put every clone on the network. Copy the example instead:

```bash
cp scripts/deerx.local.conf.example scripts/deerx.local.conf
```

```ini
PORT=8791
HOST=0.0.0.0
WORKSPACE=/srv/projects/customer-x
```

Both scripts read it. **The command line still wins** — `deerx.sh start -p 9000`
overrides the file — and whenever the file supplies a value the script says so
in one line, so a server bound to `0.0.0.0` is never a surprise. The file is
gitignored, and it is read line by line rather than sourced: a settings file
should not be able to run commands.

Binding to a non-loopback address needs at least one account; without one,
`serve` refuses to start rather than exposing an open server.

### What the scripts get right

| Situation | Behaviour |
|---|---|
| The PID was recycled and belongs to another process | The command line is verified; a foreign process is **not** killed |
| `deerx.exe` is a wrapper, the server a separate process | The PID is resolved from the port, so the wrapper is not killed leaving an orphan |
| The server was started outside the script | `status` says so rather than reporting "stopped" |
| An unrelated program holds the port | It says "not DeerX" and `start` suggests another port |
| The workspace is running on a different port | The real port is printed, not the one you asked for |

`start` succeeds when the server **responds**, not merely when the process
exists; if it cannot be reached, the last lines of the log are printed.

The probed endpoint is `/api/auth/status`. With authentication on, a protected
endpoint returns 401 and both `curl -f` and `Invoke-WebRequest` treat that as an
error — a healthy server would look unresponsive. `tests/test_scripts.py` pins
this: every path the scripts probe must be in `PUBLIC_PATHS`.
