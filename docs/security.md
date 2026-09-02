# Security model

[← Documentation](README.md) · [Türkçe](tr/security.md)

For the policy and how to report a vulnerability, see
[SECURITY.md](../SECURITY.md). This page is the technical detail.

## The boundary is the file tools, not the process

DeerX runs commands **directly on the host**, not in a container or a VM. What
is restricted is the directory the file tools can see, and which commands the
shell tool will run at all.

That means:

- An allowed command can do whatever that command does, including reach outside
  the workspace and out to the network.
- The confinement stops *the agent's file tools* from wandering. It does not
  sandbox *the processes* the agent starts.

If you need real isolation, run DeerX inside a container. This is stated plainly
because it is the thing most likely to be assumed rather than read.

## Path confinement

Every path an agent gives is expanded, resolved and checked:

```python
resolved = candidate.resolve()
if not resolved.is_relative_to(workspace.resolve()):
    raise WorkspaceError(...)
```

Resolution happens **before** the check, so `../`, symlinks and absolute paths
all collapse to a real location first. A path that lands outside is refused with
both paths named, so the agent can correct itself rather than retry blindly.

## The shell policy

Three gates, in order:

### 1. Deny list — unconditional

Destructive patterns are refused regardless of the allow list.

Matching is **position-aware**. An earlier substring match refused legitimate
code: `srv.shutdown()`, `sock.shutdown()`, `--shutdown-timeout` and even
`print('reboot note')` were all blocked because they contained a forbidden word
somewhere. A bare command name now matches only in command position.

### 2. Allow list

`[deerx.shell] allow_prefixes` in `deerx.toml`. Parsing is quote-aware, so
`python -c "import sys; sys.exit(1)"` is one `python` command and not a
`sys.exit` injection.

An empty list means only the deny list applies. That is a real choice with real
consequences — see [Configuration](configuration.md).

### 3. Approval

With `approval_mode = "ask"` (the default), you see the command before it runs,
in the terminal or in the browser. Approvals are remembered per signature for
the run, so the same command is not asked twice.

`start_service` goes through the same three gates. Starting a long-lived process
is not less dangerous than a one-off command.

### Timeout kills the tree

`subprocess.run(timeout=…)` kills only the shell. Its children survive holding
the pipes open, and `communicate()` then blocks for the command's real duration.
Measured: a 30-second command took 30 seconds under a 2-second limit — the
timeout did nothing.

The shell tool starts commands in their own process group and kills the whole
group on timeout.

## Isolated execution (optional)

By default the agent's `run_command` and `start_service` run **on the host**,
fenced by the shell allow-list. That fence protects the machine but also
blocks work the agent legitimately needs: it cannot delete a file it created
by mistake, because `rm` is not on the list.

Set `execution = "docker"` and both run inside a disposable container
instead:

```toml
[deerx]
execution = "docker"
sandbox_image = "python:3.13-slim"
sandbox_port_base = 8100
sandbox_port_count = 10
```

Inside the container the **allow-list is not applied** — there is no host to
protect, and the blast radius is a container that is deleted when the run
ends. The agent can `rm`, install packages, and kill processes.

Three constraints, all measured on Windows with Docker 29.7.2:

* `--network host` does **not** expose a container port to the Windows host.
  Ports are therefore *published* when the container is created, and a
  service must pick one from the published range. Docker cannot add
  published ports later.
* A service inside the container must bind `0.0.0.0`, not `127.0.0.1`, or the
  published port stays empty.
* "Is the port free / ready?" must be probed **inside** the container. From
  the host every published port already looks open, because Docker itself is
  listening; asking the host would report a service as ready before it
  started.

Isolation is not total on purpose: the **workspace is mounted**, so the agent
and the host file tools see the same files. That is what makes the workflow
work — and it means `rm -rf /` would still destroy the project. Catastrophic
patterns stay refused in both modes.

Two more things the container gets, both measured:

* **Resource caps.** `--memory 2g`, `--cpus 2`, `--pids-limit 512`. Without
  them a fork bomb or a memory hog would not stay in the container; it would
  take the machine down, which is the one thing isolation exists to prevent.
* **No route to your host services.** Before this was closed, a container
  could reach the host's vLLM (8008), SearXNG (8890) and **DeerX's own web
  UI** (8791) through `host.docker.internal` — an agent could have driven
  DeerX from inside its own sandbox. That name now resolves to the container
  itself. Outbound internet still works, so `pip` and `apt` do too. This is
  not a full network partition; the gateway address remains routable.

The default image is `python:3.13`, not `-slim`: measured, the slim image has
no `git`, `curl`, `gcc` or `make`, so the agent hits a wall on the first
build or `git init`. For project-specific tools set `sandbox_setup`, which
runs once when the container is created.

## Network

### `fetch_url` and SSRF

Refuses private, loopback, link-local and multicast addresses. The check runs
**after DNS resolution**, on every resolved address — a hostname that resolves
to `127.0.0.1` is caught, not trusted for looking public.

### The browser proxy

The agent's browser runs behind a filtering proxy that handles both `CONNECT`
and absolute-form requests. The URL policy is enforced there rather than in the
page, so a redirect or a sub-resource cannot escape it.

The policy carries a DNS-rebinding defence: the address the policy approved is
the address the connection uses.

### Local preview

`preview_open` accepts only `127.0.0.1:<port>`. The permission is granted
server-side — the model has no way to reach the policy list — and it is dropped
when the run ends.

`enable_web` (internet access) and `browser_allow_preview` (loopback preview)
are **separate settings**. They were once one, which meant turning off internet
access also stopped an agent from opening the application it had just written.

## Authentication

Active as soon as one user exists. A local install with no users behaves as it
always did; a server with no users **cannot bind a non-loopback address** —
`--host 0.0.0.0` refuses to start. A warning would not be enough for an endpoint
that writes files and runs commands.

The first administrator is created with a **setup token** printed only to the
server console, so whoever reaches the server first cannot claim the account.

### Passwords

`scrypt` with a per-user salt — memory-hard, and in the standard library, so no
new dependency.

The policy follows NIST SP 800-63B: minimum **8 characters**, no composition
rules. Forcing upper case, digits and symbols pushes people towards `Password1!`
and similar. Instead a known-password list is checked, and a password on it is
**accepted with a warning** rather than refused — telling an administrator
setting up their own account on their own machine "no" is paternalism, and
hiding the risk would be worse.

### The decisions and why

| Decision | Why |
|---|---|
| The error does not distinguish user name from password | Saying which was wrong enables user enumeration |
| The KDF runs even for a non-existent user | So response time does not answer "does this account exist" |
| 5-minute lockout after 8 failed attempts | Per account, so nobody can lock out someone else |
| Cookie `HttpOnly` + `SameSite=Lax`, `Secure` when the request arrived over HTTPS | Not stealable via XSS, not tripped by cross-site POST, not sent in the clear once TLS is in front |
| All sessions drop on password change | Cutting existing sessions is the point of changing a password |
| The disabled state is not revealed on a wrong password | Otherwise account status leaks without knowing the password |
| The primary admin cannot be deleted, demoted or disabled | So the last administrator cannot lock everyone out |
| Enforcement is in middleware, not per route | So a new endpoint cannot forget to add it |

Sessions are stored server-side specifically so they can be revoked. A stolen
password needs its open sessions closed immediately, and a stateless token
cannot be closed.

## The audit log

The server writes files and runs shell commands. On a shared install the
question "who did that?" needs an answer, and it needs to be answerable after
the fact — which means it must be written down *while* it happens.

`GET /api/audit` returns it; the Settings screen shows it. Administrators only,
except where no accounts exist at all — there, the whole server is already open
and hiding the log alone would protect nothing while leaving the panel dead on a
single-user install.

| Recorded | Detail kept |
|---|---|
| `login`, `logout`, `login.failed` | The name that was tried, the address, the browser |
| `run.start`, `run.stop` | The run's title, stored as a translatable key |
| `settings.change` | The **names** of the fields that changed |
| `user.*`, `password.*` | The account acted on |
| `package.build`, `knowledge.*` | The file or source |

Four decisions worth stating:

| Decision | Why |
|---|---|
| Refused sign-ins are recorded, under the name that was tried | "Ten attempts on an unknown account" is the most useful line in a security log, and the one easiest to leave out — there is no `User` object to hang it on |
| Settings changes record field names, never values | The values include API keys. A log that leaks what it is meant to protect works against itself |
| A deleted account keeps its trail; only the link is cut | Otherwise deleting a user would be the way to clear the history |
| The log is capped, and the cap is periodic | It shares the project database. Trimming on every write would spend a 5000-row scan per sign-in, so it runs every 256 rows — the row count stops growing, it just does not stop exactly on the line |

The log holds addresses and user-agent strings. That is what makes it useful and
also what makes it worth restricting: it is one of the few places in DeerX where
reading is itself a privilege.

## Secrets

| Where | What holds |
|---|---|
| At rest | `.env` in the workspace; gitignored, only `.env.example` is tracked |
| Over the API | Write-only. Reading settings returns `has_*` booleans, never values |
| In delivery packages | Excluded by pattern and **named in the manifest** |
| In the repository | No key appears in the working tree or in git history |

Delivery exclusion covers `.env`, `*.pem`, `*.key`, `id_rsa*`, `credentials*`,
`service-account*.json` and similar, while keeping `.env.example`. Patterns
apply to **every** path segment, so a monorepo's `frontend/node_modules/` is
excluded exactly like the one at the root.

Every excluded secret is listed in the manifest as `DAHIL EDILMEDI` — visible,
not silent. A test asserts no secret value appears in the raw bytes of a
produced zip.

## Rendering

- Artifact markdown is rendered with raw HTML injection disabled.
- HTML mockups are shown in a `sandbox` iframe.
- API error text reaches the browser as data, not markup.

## What is deliberately not defended

- **Prompt injection through indexed content.** Specifications, source comments
  and fetched web pages are all read by a model. Text that reaches a model can
  attempt to steer it. Do not point a workspace at content you do not trust.
- **An operator who sets `approval_mode = "auto"`** gets unattended execution.
  That is the setting's purpose.
- **Anything an allowed command does.** The allow list is the decision point;
  after that, the command is the command.

## See also

- [SECURITY.md](../SECURITY.md) — policy and private reporting
- [Configuration](configuration.md) · [Agent tools](tools.md) · [Delivery packages](delivery.md)
