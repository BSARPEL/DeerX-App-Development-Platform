# Contributing

Thanks for looking. This document says how the codebase works so a change you
send fits in without a round of style notes.

[Türkçe](docs/tr/CONTRIBUTING.md)

## Setup

```bash
uv sync --extra embed --extra dev
uv run deerx doctor
```

Optional extras: `--extra browser` for the Playwright-backed tools.

## The two checks

```bash
uv run pytest
```

```bash
uv run ruff check src tests
```

Both must pass. There is no separate formatter step — `ruff check` is the bar,
and the codebase is not `ruff format`-clean by choice.

### Run them before you push

One script runs both. **This is the repository's only verification** — there is
no CI — so a broken push is caught here or not at all:

```bash
./scripts/check.sh          # lint + the whole suite
./scripts/check.sh --fast   # skips the tests that spawn real processes (~26s)
```

```powershell
.\scripts\check.ps1
.\scripts\check.ps1 -Fast
```

To have it run automatically on every `git push`, point git at the versioned
hooks directory once:

```bash
git config core.hooksPath .githooks
```

`git push --no-verify` skips it when you need to.

### Types

`mypy` is installed with the `dev` extra and configured in `pyproject.toml`, but
`check.sh` does **not** run it and your push is not blocked by it:

```bash
uv run mypy src/deerx
```

There is a baseline of 28 findings. A check that does not pass is a check
everyone learns to ignore, so it stays out of the gate until that reaches zero —
at which point it belongs in `check.sh`. It is worth running anyway: the first
run found a real defect (the setup probe called a method that does not exist, so
the embedding model was never downloaded and the swallowed `AttributeError`
looked like a failed download).

### Across Python versions

Until 2026-09-02 a GitHub Actions workflow ran the same two commands across
Ubuntu, Windows and macOS on Python 3.11 and 3.13. It has been removed — the
checking stays on your machine. Half of what it covered comes back with one
flag:

```bash
./scripts/check.sh --pythons
```

```powershell
.\scripts\check.ps1 -Pythons
```

That runs the suite again under **3.11 and 3.13** in side environments
(`.venv-check-*`, gitignored) installed with `--extra dev` only — so it also
catches a test that quietly depends on an optional extra. Your own `.venv` is
never touched. About nine minutes for all three runs.

What no single machine can give you is the **operating system** axis, and that
gap is real rather than theoretical: a single test expecting a Windows-only
exit code stayed red on Linux and macOS for fifteen runs while looking green on
the machine it was written on. If your change touches process handling, paths,
shell scripts or anything the standard library implements differently per
platform, run the suite somewhere else too before you trust it.

## House rules

**Comments and identifiers are Turkish, ASCII-folded.** `calisma_alani`, not
`working_directory` or `çalışma_alanı`. This is consistent throughout; please
match it rather than starting a second convention. User-facing *strings* are a
different matter — see below.

**Every user-facing or model-facing string goes through the catalog.**

```python
raise ToolError("Dosya bulunamadi")        # no
raise ToolError(t("fs.not_found"))         # yes — text lives in i18n.py
```

`tests/test_no_hardcoded_turkish.py` scans the source with an AST walk and fails
if a string reaching a user or the model is hardcoded. Both languages must be
filled in, and their `{placeholders}` must match — that is also tested.

Tool *descriptions* are the exception: the Turkish stays inline in the tool's
class because that is where the behaviour is documented, and the English lives
in `src/deerx/tools/descriptions_en.py`. A test checks that every tool and every
described parameter has both.

**Comments explain why, not what.** The codebase leans on this heavily. A
comment that restates the line above it is noise; a comment that records the
measurement or the failure that produced the line is the reason the code is
readable. Look at `tools/shell.py` or `pipeline/orchestrator.py` for the tone.

**Write the failing test first.** Not as ceremony — as a check that the test
actually bites. A test that passes against the unfixed code tests nothing, and
that has happened here before.

## Tests

`tests/` mirrors the package. A few files carry specific jobs:

| File | What it guards |
|---|---|
| `test_regressions.py` | One test per bug that once shipped silently |
| `test_no_hardcoded_turkish.py` | Every user/model-facing string is in the catalog |
| `test_i18n_py.py` · `test_i18n.py` | Catalog shape, placeholder parity, that switching actually switches |
| `test_scripts.py` | The management scripts, including that probed paths are public |
| `test_web.py` | The HTTP API, the SSE loop, the palette and the design scale |

Tests use a fake LLM client (`tests/conftest.py`); nothing in the suite makes a
real model call or needs a network.

## Adding things

The long form of this section, with the reasoning behind each constraint, is
[Extending DeerX](docs/extending.md). If something is broken rather than
missing, [Troubleshooting](docs/troubleshooting.md) lists the symptoms that have
actually occurred here.

**A new tool** — subclass `Tool` in the right `tools/` module, add it to
`ALL_TOOLS` so `build_registry()` picks it up, put the Turkish description on
the class and the English in `descriptions_en.py`, and add its name to
`TOOLSETS` in `tools/__init__.py` for every role that needs it —
`agents/roles.py` reads that map, it does not hold it. Route every error
through `t()`. The long form is [Extending DeerX](docs/extending.md).

**A new pipeline phase** — add it to `Phase` in `pipeline/models.py`, to
`Phase.ordered()`, to `PHASE_DELIVERABLE` in the orchestrator if it produces an
artifact, and add `phase.<id>` / `agent.<id>` / `produces.<id>` keys to **both**
`i18n.py` and `web/static/i18n.js`. A test verifies the two catalogs cover the
same phases.

**A new setting** — add the field to `Settings` in `config.py`, to the template
in `templates/deerx.default.toml`, and to `SETTING_FIELDS` in `web/app.py` if it
should be editable from the interface. Unknown keys in `deerx.toml` produce a
warning with a spelling suggestion, so a typo does not vanish.

**A new agent prompt** — `agents/prompts/<role>.md` and
`agents/prompts/en/<role>.md`. Artifact file names inside prompts stay Turkish:
the pipeline matches deliverables by name, so translating them would break the
check that a phase produced something.

## Commits

Messages are in Turkish, following the existing log: a subject line that says
what changed and why it mattered, then a body that explains the failure the
change addresses. `git log` is documentation here — please keep it that way.

Branch off `main`. One concern per pull request.

## Reporting a security issue

Do not open a public issue. See [SECURITY.md](SECURITY.md).
