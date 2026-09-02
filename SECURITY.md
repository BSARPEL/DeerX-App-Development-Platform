# Security policy

## What DeerX actually does

Read this before you deploy anything. DeerX is not a chat wrapper — it is an
agent that, by design:

- **writes and edits files** in a workspace directory,
- **runs shell commands** (tests, builds, package installs, git),
- **starts long-lived background processes** (dev servers),
- **drives a real Chrome** on the host and can open pages,
- **reaches the internet** for research when web access is enabled.

Those capabilities are the product. They are also the threat model. Treat a
DeerX server the way you would treat an SSH session on that machine.

## Threat model

| Boundary | What holds | What does **not** hold |
|---|---|---|
| File operations | Confined to the workspace; a resolved path that escapes it is refused | Nothing stops a *command you allowed* from touching the rest of the disk |
| Shell | Deny list (unconditional) → allow list → per-command approval | Commands run directly on the host, **not** in a container or VM |
| Network (agent tools) | `fetch_url` refuses private/loopback/link-local addresses (SSRF) | An allowed shell command has unrestricted network access |
| Browser | A filtering proxy enforces the URL policy, with DNS-rebinding defence | The browser uses a real Chrome profile on the host |
| Local preview | Only `127.0.0.1:<port>`, granted per run, dropped when the run ends | — |
| Web server | Binds `127.0.0.1` by default; a non-loopback `--host` refuses to start with no users | — |
| Delivery packages | Secret patterns are excluded and listed in the manifest | — |
| Embeddings | Local ONNX; document text is not sent anywhere to be embedded | Model calls themselves go to whatever provider you configured |

**The sandbox boundary is the file tools, not the process.** If you need real
isolation, run DeerX inside a container or a VM. This is stated plainly because
it is the single most likely thing to be misunderstood.

## Running it safely

- Keep `approval_mode = "ask"` (the default) unless you are watching the run.
- Keep the server on loopback. If you must expose it, create users first —
  the server refuses a non-loopback `--host` with no users configured, and that
  refusal is deliberate, not a bug to work around.
- Use a dedicated workspace directory, not your home directory or a repo you
  cannot afford to have modified.
- Review `[deerx.shell] allow_prefixes` in `deerx.toml`. It is an allow list;
  everything you add is something an agent may run without you seeing it first
  when approvals are off.
- Do not point a workspace at a repository whose contents you do not trust.
  Specifications, source comments and web pages are all inputs the model reads,
  and text that reaches a model can attempt to steer it.

## Secrets

- API keys live in `.env` inside the workspace. `.env` is gitignored; only
  `.env.example` is tracked.
- Keys are **write-only through the web API** — reading settings returns
  `has_anthropic_key: true/false`, never the value.
- Delivery packages exclude `.env`, `*.pem`, `*.key`, `id_rsa*`, `credentials*`,
  `service-account*.json` and similar. Every excluded file is named in the
  manifest so the exclusion is visible rather than silent. A test asserts that
  no secret value appears in the raw bytes of a produced zip.
- Passwords are stored with `scrypt` and a per-user salt. Sessions are
  server-side so they can be revoked.

## Reporting a vulnerability

Please report security issues **privately**, not as a public issue.

Use GitHub's [private vulnerability reporting][gh-private] on this repository
(Security → Report a vulnerability). If that is unavailable, open an issue that
says only that you have a security report and asks for a contact channel —
without details.

[gh-private]: https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability

Please include: what you did, what happened, what you expected, and the version
or commit. A proof of concept helps enormously.

**What counts as a vulnerability here:** a way to escape the workspace
confinement with the default configuration, to bypass the shell policy or the
approval gate, to make a secret reach a delivery package or the API surface, to
authenticate as another user, or to reach an authenticated endpoint without a
session.

**What does not:** that an allowed shell command can do what that command does;
that an operator who set `approval_mode = "auto"` gets unattended execution;
that exposing the server to a network you do not trust is dangerous. Those are
documented properties, above.

## Supported versions

This project is pre-1.0. Fixes land on `main`; there are no backports.
