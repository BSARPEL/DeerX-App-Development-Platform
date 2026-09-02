# DeerX documentation

[← Back to the project README](../README.md) · [Türkçe](tr/README.md)

## Start here

| | |
|---|---|
| **[Getting started](getting-started.md)** | Install, configure a provider, run the pipeline for the first time |
| **[The pipeline](pipeline.md)** | The 13 phases, the agent cast, lane routing, the question gate |
| **[Model providers](providers.md)** | Local vLLM, Ollama, OpenAI, Anthropic — and what actually differs |

## Using it

| | |
|---|---|
| **[Web interface](web-ui.md)** | Every screen, what it shows and why it is arranged that way |
| **[CLI reference](cli.md)** | Every command, its flags, its exit codes, and the management scripts |
| **[Configuration](configuration.md)** | `deerx.toml`, environment variables, precedence, language |
| **[Delivery packages](delivery.md)** | The readiness gate, secret exclusion, the archive layout |
| **[MCP server](mcp.md)** | Exposing the knowledge base and pipeline to another agent |
| **[Troubleshooting](troubleshooting.md)** | Symptoms that actually occurred, their measured causes and fixes |

## How it works

| | |
|---|---|
| **[Agent tools](tools.md)** | All 39 tools, and how an agent runs and tests what it writes |
| **[Architecture](architecture.md)** | The module map and the reasoning behind each decision |
| **[Security model](security.md)** | Confinement, the shell policy, authentication, secret handling |
| **[Bilingual architecture](i18n.md)** | How one setting reaches the UI, the CLI, the tools and the prompts |
| **[Verification status](verification.md)** | What was verified by running it, and what was not |
| **[Extending DeerX](extending.md)** | Adding a tool, a phase, a provider or a language |
| **[The project's own knowledge base](knowledge-base.md)** | Index DeerX's docs and source, then ask a model about them |

## Conventions in this documentation

Anything stated as a measurement was measured — the numbers come from real runs,
and where a number contradicts an assumption the assumption is named. Where
something is **not** verified, it says so; see
[Verification status](verification.md).

Artifact file names are Turkish in both languages (`analiz-raporu.md`,
`mimari.md`, `gelistirme-plani.md`). This is deliberate: the pipeline matches a
phase's deliverable by file name, so translating them would break the check that
a phase actually produced something.
