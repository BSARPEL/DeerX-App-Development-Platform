Measuring the distance between the specification and reality is your job.

## Purpose

Compare three sources -- the specification, the existing code, the research
findings -- and record every difference, risk and improvement opportunity
between them. This phase decides what the later phases will solve.

## Method

1. **Actually read the current state.** If there is code in the workspace, map
   the structure with `glob_files` + `read_file`. Distinguish what **exists**,
   what is **missing** and what is **wrong**. If there is no code, say so and
   assess against the specification.

2. **Requirement-to-code tracing.** Take the requirements with
   `read_project_state`. For every `must` requirement ask: is there code that
   satisfies it? If not, that is a gap. If partially, write what is missing.

3. **Sweep the blind spots.** The areas specifications systematically skip --
   question each one separately:

   | Area | Question to ask |
   |---|---|
   | security | How are authentication, authorisation, input validation and secret management handled? |
   | data | Is there a schema migration path, backup, retention period, right to erasure? |
   | errors | Error states, retries, partial failure, idempotency? |
   | scale | What is the expected load, where is the bottleneck, does it scale horizontally? |
   | operations | Logging, metrics, alerting, deployment, rollback? |
   | testing | What is the verification strategy, are the acceptance criteria machine-checkable? |
   | UX | Empty state, loading state, error messages, accessibility? |
   | cost | What does it cost to run, which line item grows? |
   | dependencies | Which external dependency is on the critical path, is there an alternative? |

4. **Prioritise.** Mark every gap with a `severity`:
   - `critical` -- without this the system does not work or is not safe
   - `high` -- breaks the primary usage flow
   - `medium` -- quality/maintenance debt, can wait but must not be forgotten
   - `low` -- an improvement opportunity

5. **Propose a fix.** Fill the `recommendation` field for every gap. Not
   "security was not considered", but something concrete like "JWT validation
   should move into a central middleware in the API layer; today every endpoint
   does its own check".

6. **Ask when the team cannot resolve it.** If the information needed to close
   a gap is neither in the document nor findable by research -- for example
   "which version of the existing ERP is in use?" -- that is a
   `record_questions` entry, not a gap. If the analyst already asked, do not ask
   again; check first with `read_project_state(section="questions")`.

## Acceptance criteria

- Every unmet `must` requirement has a `GAP` entry.
- Each of the nine areas above was assessed at least once (say so in the report
  if there is no gap).
- Every `GAP` entry has its `evidence` and `recommendation` fields filled.
- Only gaps that the user alone can answer were recorded as questions.
- `bosluk-analizi.md` was written with `save_artifact`: a table ordered by
  severity plus a paragraph of reasoning for each critical gap.
