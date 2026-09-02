# Regenerating the screenshots

The images in [`docs/images/`](../../docs/images) are real screenshots of the
real interface. They are **not** taken from anyone's project: a demo workspace
is built from scratch first, so nothing in the repository shows a private spec
or a home directory.

Three scripts, in order:

| Script | What it does |
|---|---|
| `demo_workspace.py` | Builds `demo-en` and `demo-tr`: a plausible field-service spec, its requirements, gaps, questions, decisions, plan, workflow and artifacts — plus three accounts and an audit log to go with them. No model is called. |
| `demo_documents.py` | Indexes five documents into each, with the `hash` embedder — no download, no network. |
| `capture.py` | Signs in, drives Playwright over both servers and writes nine PNGs per language. |

```bash
# 1. Build the two demo workspaces OUTSIDE your home directory
#    (the workspace path is now on screen in every shot — see below)
uv run python scripts/screenshots/demo_workspace.py C:/deerx-demo
uv run python scripts/screenshots/demo_documents.py C:/deerx-demo

# 2. Serve them — English on 8781, Turkish on 8782
uv run deerx serve --port 8781 --workspace C:/deerx-demo/demo-en &
uv run deerx serve --port 8782 --workspace C:/deerx-demo/demo-tr &

# 3. Capture
uv run python scripts/screenshots/capture.py
```

Each workspace needs a `deerx.toml` pinning its language and the offline
embedder — `demo_workspace.py` does not write it, because the language is what
decides which set of images you get:

```toml
[deerx]
language = "en"          # or "tr"

[deerx.rag]
embedding_provider = "hash"
embedding_dim = 128
```

## Rules the capture enforces

- **Dark theme, set directly.** Clicking the toggle blindly went the wrong way
  whenever `data-theme` was unset and the system preference was already dark.
- **No home directory in frame.** The workspace path now sits in the bottom-left
  rail, so it is in *every* frame, not just Settings — one slip leaks it into
  eighteen images at once. `capture.py` checks the rail at the start of each
  language run and refuses to shoot a path containing `Users`, `home/` or
  `Documents`. Use a neutral workspace path (`C:\deerx-demo`, `/tmp/deerx-demo`).
- **The demo workspace has accounts.** The audit log is only worth a screenshot
  with people in it, so `demo_workspace.py` creates `deniz` (admin), `mert` and
  a since-deleted `elif`, and seeds a log. That means the demo server asks for a
  sign-in; `capture.py` logs in with the throwaway password defined at the top of
  `demo_workspace.py`. It is a fixture, not a credential — the workspace is
  rebuilt from scratch every time.
- **Both languages, always.** `tests/test_docs.py` fails if an `-en` image has
  no `-tr` counterpart, or if any image is referenced but missing.

## When to rerun

Whenever a screen changes shape — a new panel, a reordered layout, a renamed
label. A screenshot that no longer matches the interface is worse than none:
it teaches the reader something untrue.
