# Delivery packages

[← Documentation](README.md) · [Türkçe](tr/delivery.md)

When the implementation is finished, DeerX delivers the work as **a single
zip** — but only after verifying it is deliverable. Packaging half-finished
work is worse than not packaging at all, because it looks finished.

```bash
uv run deerx package
```

It also runs as a phase (`deerx phase package`) and needs no model: the whole
thing is deterministic.

## The readiness gate

Packaging is **refused** (exit code `2`) if any of these hold:

| Blocker | Why |
|---|---|
| The plan is empty | There is no defined work to package |
| A task failed | Broken code is not delivered |
| A task is unfinished | Incomplete work is not sent as "done" |
| An unanswered blocking question | A wrong assumption may already be in the code |

These are reported as **warnings** but do not stop it: open critical or high
gaps, and a QA or review phase that never ran.

To package anyway:

```bash
uv run deerx package --force
```

The warnings and blockers are then written into the manifest, so the delivered
archive carries its own caveats rather than presenting as clean.

**Forcing is only this flag.** `deerx run --force` means something else
entirely — "re-run a completed phase" — and does not open the delivery gate. A
user who wants to redo a run has not asked to ship a half-finished project, and
one flag name doing both jobs would eventually be the reason something shipped
that should not have.

## Secret exclusion

These never enter the archive:

```
.env  *.pem  *.key  id_rsa*  credentials*  service-account*.json  ...
```

`.env.example` and similar templates are kept — they are documentation, not
secrets.

Also excluded: `.git/`, `.deerx/`, `node_modules/`, `.venv/`, `__pycache__/`,
build outputs and caches. These patterns apply to **every** segment of a path,
so a monorepo's `frontend/node_modules/` is excluded exactly like the one at the
root.

Every excluded secret file is listed in the manifest as `DAHIL EDILMEDI`. The
exclusion is visible rather than silent — someone reading the manifest can see
that a file was deliberately withheld, instead of wondering why it is missing.

One test's entire job is to assert that no secret value appears in the raw bytes
of a produced zip.

## Archive layout

```
<project>-20260828-1430.zip
└── <project>/
    ├── TESLIMAT.md        scope, requirement tracing, tasks, exclusions
    ├── README.md          the project's own files
    ├── src/ ...
    ├── tests/ ...
    └── belgeler/          analysis, architecture, plan, QA and review reports
```

The package is written to `.deerx/teslimat/` and recorded in the project memory
as an artifact of kind `package`.

Earlier delivery zips are never taken into a new package. If they were, each
package would wrap the previous one and the size would compound with every
release.

## `TESLIMAT.md`

The manifest is the delivery's own account of itself:

```
Status · Counts · What was done (phase by phase, with each agent's summary) ·
Requirements met · Tasks completed · Architectural decisions ·
Documents · Package contents · What was left out · Open matters
```

"What was left out" and "open matters" are there on purpose. A delivery that
hides its own gaps is the failure mode this whole gate exists to prevent.

## In the interface

A zip is an **attachment**, not text. Dumping an archive's raw bytes on screen
produces a screen of garbage; instead the package appears as a downloadable card
with its `TESLIMAT.md` rendered underneath as a report.

```
🗜  <project>-20260828-1430.zip
    delivery archive · 12.1 KB · 24 files          [ Download ]

    ▸ Contents — 24 files          (collapsed)

    WHAT WAS DONE FOR THIS PACKAGE
    ──────────────────────────────
    Status · Counts · What was done · Requirements met · ...
```

The same rule covers `.png`, `.pdf` and other binary artifacts. On the MCP side,
`deerx_artifact <package.zip>` returns this report rather than bytes.

Manual packaging creates a single-step run record — without it, the package
would belong to no run and would be unreachable from the Runs view.

## See also

- [The pipeline](pipeline.md) — phase 11 in context
- [Security model](security.md) — how secret patterns are applied
- [CLI reference](cli.md) — flags and exit codes
