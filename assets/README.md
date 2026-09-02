# Brand assets

The 2000×2000 masters. Nothing in the application loads these — they are the
source the smaller files are cut from, kept here so a future size does not have
to be redrawn.

| File | Size | Used for |
|---|---|---|
| `logo.png` | 2000×2000 | Master, light backgrounds |
| `logo_dark.png` | 2000×2000 | Master, dark backgrounds |
| `favicon.ico` | multi-size | Master for the browser icon |

What the application actually ships:

| File | Size | Where |
|---|---|---|
| `src/deerx/web/static/logo.png` | 128×128 | Top bar, login screen, both READMEs |
| `src/deerx/web/static/logo-dark.png` | 128×128 | The same, on dark backgrounds |
| `src/deerx/web/static/favicon.png` | 64×64 | Browser tab |

Screenshots of the interface are not here; they live in
[`docs/images/`](../docs/images) and are regenerated from a demo workspace
rather than from anyone's real project.
