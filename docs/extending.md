# Extending DeerX

[← Documentation index](README.md) · [Türkçe](tr/extending.md)

## What is meant to be extended

Five things are designed to be added to without touching the rest:

| Extension point | Where it lives | What it costs you |
|---|---|---|
| A tool | `src/deerx/tools/` | A class, a registry line, a role line, a translation, a test |
| A pipeline phase | `src/deerx/pipeline/` + `src/deerx/agents/` | An enum member, three map entries, a prompt |
| A model provider | `src/deerx/llm/` | A client class and a dispatch branch |
| A search provider | `src/deerx/tools/web.py` + `browser.py` | A search function and a dispatch branch |
| A language | Three catalogues + a prompt directory | Complete parity, enforced by tests |

Everything else — the orchestrator loop, the approval gate, the workspace fence,
the delivery gate — is meant to be read before it is changed, because each piece
exists in response to something that went wrong. The comments say which thing.

## Adding a tool

### 1. Write the class

A tool is a class with four attributes and a `run` method. It lives in whichever
module matches its subject: `knowledge.py`, `project.py`, `filesystem.py`,
`shell.py`, `services.py`, `web.py`, `images.py`, `browser.py` — or a new module
if it is genuinely a new subject.

```python
class CountWords(Tool):
    name = "count_words"
    description = """
    Bir dosyadaki sozcuk sayisini bildirir.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Calisma alanina goreli yol."},
        },
        "required": ["path"],
    }

    def run(self, ctx: ToolContext, path: str) -> ToolResult:
        hedef = ctx.resolve(path)
        return ToolResult(content=str(len(hedef.read_text().split())))
```

Four things about that shape are not optional:

- **`description` and the schema descriptions go to the model.** They are the
  tool's only documentation as far as the agent is concerned. Write them as
  instructions, not as summaries — say when to use the tool and when not to.
- **Resolve paths through the context**, never with a bare `Path`. That is the
  workspace fence; a tool that opens a path directly has stepped around it.
- **Raise `ToolError` for recoverable problems.** It is returned to the model as
  a message it can act on. An uncaught exception ends the phase.
- **Set `dangerous = True`** if the tool writes outside the obvious, runs
  commands, or costs money, and call `ctx.approve` inside `run`. The flag alone
  does nothing — the tool asks.

If the tool produces something the model must **see** rather than read about —
a screenshot, a rendered chart — put the file in `ToolResult.images`. The
transport moves it into a following `user` message, because the OpenAI wire
format does not allow images on a `role: "tool"` message. Reporting "screenshot
saved" leaves the model blind to what it just built.

### 2. Register it

Add the class to its module's export list, and make sure that list is spliced
into `ALL_TOOLS` in `src/deerx/tools/__init__.py`:

```python
ALL_TOOLS: list[Tool] = [
    *KNOWLEDGE_TOOLS,
    *PROJECT_TOOLS,
    ...
]
```

`build_registry()` reads that list and nothing else. A tool that is not in it
does not exist.

### 3. Give it to a role

No agent sees every tool. `TOOLSETS` in the same file maps each of the twelve
roles to the tool names it may call:

```python
TOOLSETS: dict[str, list[str]] = {
    "analyst": ["search_knowledge", "read_document", ...],
    ...
}
```

A wide tool list raises both the cost and the odds of the model picking the
wrong tool, so add a name to the smallest set that needs it.

Two exclusions are deliberate and must stay:

- **The researcher has no `write_file` and no `run_command`.** It reads web
  pages, and a web page can say "ignore your previous instructions and run this".
  It may read, browse and take notes. That is enough.
- **The `live` role has no `write_file` and no `edit_file`.** A release gate
  that can edit the thing it is gating is not a gate.

### 4. Translate the description

Tool descriptions are written in Turkish in the class, where they double as the
code's own documentation. The English versions live in
`src/deerx/tools/descriptions_en.py` and override the class attributes when the
agent language is English:

```python
ENGLISH = {
    "count_words": {
        "": "Reports the number of words in a file.",
        "path": "Path relative to the workspace.",
    },
}
```

The `""` key is the tool description; the rest are parameter descriptions. Note
that `spec()` copies the schema rather than mutating it — the schema is a class
attribute, and mutating it in place would freeze the language for the whole
process on the first call.

### 5. Test it

Two tests, and the second one is the one people forget:

- **The tool does what it says.** Call `run` and check the result.
- **The tool is reachable.** Assert that it is in `build_registry().names()` and
  in the toolset of the roles that need it. A perfectly correct tool that no
  role can call is a tool that never runs — and a test of `run` alone passes
  happily in that state.

Then update `docs/tools.md` and its Turkish twin. The tests will make you: the
total tool count and the per-role counts are both pinned to the code, in both
languages.

## Adding a pipeline phase

### The enum and the maps

Phases are an ordered `StrEnum` in `src/deerx/pipeline/models.py`. Add the member
in the position it runs:

```python
class Phase(StrEnum):
    INGEST = "ingest"
    ANALYZE = "analyze"
    ...
```

Then three maps in `src/deerx/pipeline/orchestrator.py`:

```python
PHASE_ROLE: dict[Phase, str] = {Phase.ANALYZE: "analyst", ...}
PHASE_DELIVERABLE: dict[Phase, tuple[str, str]] = {
    Phase.ANALYZE: ("analiz-raporu.md", "gereksinimler ve analiz raporu"),
    ...
}
```

A phase that runs no agent — `INGEST` and `PACKAGE` are deterministic — appears
in neither map.

### The role and its prompt

A role is a name in `TOOLSETS`, an iteration budget in `ITERATION_BUDGET`
(`src/deerx/agents/roles.py`), and a prompt file. Prompts live as markdown,
Turkish in
`src/deerx/agents/prompts/` and English in `src/deerx/agents/prompts/en/`, with
`_shared.md` prepended to both. Both files must exist; a missing translation is
a missing agent, not a fallback.

### The deliverable contract

Every agent phase must name a file it will leave behind. This is enforced, and
the enforcement was added for a measured reason: in one run `assess` spent three
turns reading files and stopped, `mockup` made three searches in two turns and
stopped. Both were marked `done`, neither produced a line, and the architect
then had to work from "no mockup, empty codebase".

The expected output had been named only in the prompt, and nothing checked it.
The name in `PHASE_DELIVERABLE` is where the contract is now enforced. Give your
phase one, and pick a glob (`mockup-*.html`) if the count is not fixed.

## Adding a model provider

`src/deerx/llm/__init__.py` dispatches on `settings.provider`:

```python
provider = settings.provider
if provider == "anthropic":
    ...
if provider == "openai":
    ...
raise ConfigError(t("setup.unknown_provider", provider=provider))
```

Most endpoints do not need a new provider — anything that speaks the Chat
Completions API is reached with `provider = "openai"` and a `base_url`. That
covers vLLM, Ollama, LM Studio, llama.cpp and the hosted OpenAI-compatible
services. Write a new client only for a genuinely different wire format.

If you do, the client must return the shape in `src/deerx/llm/base.py`, and two
fields matter more than they look:

- **`arguments_ok`** on a tool call — set it to `False` when the arguments could
  not be parsed. That is how the agent loop recognises a response truncated in
  the middle of a tool call rather than treating it as a finished turn.
- **`ToolOutcome.images`** — the path by which a tool's screenshot reaches the
  model. If your transport drops it, the model works blind.

Add the provider to the `Literal` in `src/deerx/config.py` so an unknown value
fails at load time rather than at the first call, and register a price row in
`src/deerx/llm/pricing.py` if the endpoint bills.

## Adding a search provider

Search has two entry points and they are easy to confuse — I edited the wrong
one and the change did nothing until an end-to-end test caught it.

- `src/deerx/tools/web.py` holds the per-provider search functions
  (`_search_searxng`, `_search_google`, and so on).
- `src/deerx/tools/browser.py` holds `WebSearch._keyed`, which **dispatches** on
  `settings.search_provider`. This is the registered tool.

Write the function in `web.py`, add a branch in `_keyed`, extend the
`search_provider` `Literal` in `config.py`, and add the provider to the
keyless-provider set in the web UI if it needs no API key — otherwise a fresh
install will warn that search does not work next to a search that works.

Then test it end to end, through the tool, not through the function. A unit test
of the function passes whether or not anything calls it.

## Adding a language

Adding a third language is the largest of these changes, because parity is
enforced rather than encouraged.

### The three catalogues

| Catalogue | Reaches |
|---|---|
| `src/deerx/i18n.py` (`CATALOG`) | The CLI, the logs, the server messages |
| `src/deerx/web/static/i18n.js` | Every string in the web UI |
| `src/deerx/tools/descriptions_en.py` | The tool descriptions the model reads |

Keys are `area.event`. The key sets must match across languages exactly —
`tests/test_i18n_py.py` locks the Python catalogue, and a companion test locks
the JavaScript one, including a check for duplicate keys. That check exists
because I introduced a duplicate myself: a new `settings.searchBrowser` key
collided with an existing dropdown label, and the later definition silently won.

### The prompt directory

Agent prompts are files, not catalogue entries. Each language gets a full
directory with one markdown file per role plus `_shared.md`. There is no
fallback to another language — a role without a prompt in the selected language
cannot run, which is better than an agent silently reasoning in the wrong
language.

### The documentation

`docs/` for English, `docs/tr/` for Turkish. `tests/test_docs.py` requires that
both languages carry the same page set **and identical heading outlines** — the
same sequence of heading depths, in the same order. A section added to one
language and not the other fails the suite.

Artifact file names stay Turkish in every language (`analiz-raporu.md`,
`mimari.md`, `gelistirme-plani.md`). The pipeline matches a phase's deliverable
by file name; translating them would break the check that a phase produced
anything at all.

## What the test suite will demand

Before a change lands, these will run against it:

- **Link integrity.** Every relative link in every markdown file must resolve.
- **Translation parity.** Same pages, same headings, same catalogue keys, both
  directions.
- **Pinned numbers.** The total tool count, the per-role tool counts and the
  test count are compared against the code. Eight hand-maintained copies of one
  number cannot stay correct; the Turkish README once claimed 558 tests when
  there were 997.
- **No personal absolute paths** in any published document.
- **Lint.** `ruff` over the whole tree.

Run all of it in one go:

```bash
bash scripts/check.sh
```

`--fast` skips the slow tests for the inner loop. `--pythons` runs the suite
again under Python 3.11 and 3.13 in side environments — worth it before a
release, since there is no CI to catch a version difference for you. The
pre-push hook in `.githooks/` runs the full set.

## House style

The code is written in Turkish — identifiers, comments and docstrings — with
English reserved for the strings that reach an English-speaking user or model.
Match the surrounding file rather than importing another convention into it.

Two conventions carry more weight than style:

- **A comment explains why, not what.** Most comments in this codebase name the
  thing that went wrong and the measurement that proved it. `OLCULDU:` marks a
  claim that was measured rather than assumed. If you write one, measure it.
- **A test must be able to fail.** Break the thing on purpose and watch the test
  go red before you keep it. This session produced three tests that passed for
  the wrong reason — one re-implemented the logic it was checking, one never
  spawned the grandchild it claimed to kill, and one used a hostname that does
  not resolve, so a DNS guard masked the assertion. All three looked fine.

## Before you push

- `bash scripts/check.sh` is green.
- New or changed behaviour has a test that fails without the change.
- Documentation updated in **both** languages, with matching outlines.
- No secret in the diff. Keys belong in `.env`, which is gitignored, and the
  delivery packager excludes them from archives as well.
- The commit message says why, in the same voice as the rest of the history.

[Verification status](verification.md) is where a claim goes when it has been
measured. If you verified something by running it, record it there — and if you
did not, that page is also where saying so belongs.
