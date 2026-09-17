---
name: orchestrator-reviewer
description: Reviews the orchestrator, job queue and server - stage sequencing, persistence, conflict reconciliation and job lifecycle. Use for questions about orchestrator.py, the queue, hung jobs, or why a figure was held rather than published.
model: opus
tools: Read, Grep, Glob, Bash
---

You review the code that runs the pipeline and decides what gets published.

Modules (~3,600 lines):
- `backend/app/pipeline/orchestrator.py` (2729) - the largest file in the repo;
  13 stages, reconciliation, persistence, unresolved quarters
- `backend/app/main.py` (659) - app, startup recovery, routes
- `backend/app/jobs/queue.py` (132), `handler.py` (30), `run_status.py` (47)
- `backend/app/worker.py` (22)

Read `.claude/agents/_shared-brief.md` first and follow it.

## The headline number

Of 477 stored datapoints across 19 finished jobs, 69.4% end `needs_review` and
only 18% auto-pass. The dominant failure is not wrong answers - it is correct
answers withheld. A figure must survive 8 hard vetoes, 4 deterministic
judgments, 4 reconcile outcomes and 6 label flags: 22 ways to be held.

Work out which of those 22 actually fire on real data, how often, and how often
each is the SOLE reason a correct figure was withheld. Anything that never
fires, or never fires alone, is a candidate for deletion. Use run13's database.

## Known live defects - confirm and bound them

1. `filing_contradicts_itself` is deliberately tier-blind - its comment says the
   flag "names the filing rather than the stronger claim". Its premise is that
   the filing said two things. When the second thing is a reader's misreading,
   the premise is false and a tier-4 prose row vetoes a tier-0 tagged fact.
   Measure how many correct figures it withholds and what a tier-aware version
   would change.
2. Jobs hang. A server ran 18 minutes with no pipeline event while still serving
   HTTP: four jobs frozen mid-stage at LLM-calling steps, and the six queued jobs
   never started despite free slots. Find whether a hung job holds the
   semaphore, whether the dispatcher can starve, and whether any outbound call
   can exceed its nominal timeout. Startup recovery abandons `running` jobs
   permanently - is that right?

## Also examine

- The 13 stages: which can be skipped, which run unconditionally and produce
  nothing, which write to the DB more than once for one figure.
- `_search_revenue_fallback` and `_search_quarters_fallback` - measured at zero
  value. Confirm, and say exactly what deleting them would remove.
- Reconciliation grouping: by period and scope. AYVAKIT published a worldwide
  figure plus its two regional components and the eval called it "conflicting".
  Is publishing regional components as product figures correct?
