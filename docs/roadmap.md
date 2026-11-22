# MCAD roadmap

## Completed and verified locally

- The existing regression suite passes representative tests for workbook import, public incident reads, authenticated candidate decisions, relationship guards, and SQLite migrations and reviewed updates. Frontend data and form tests pass with the existing local changes included. Verification for this baseline means the named automated checks passed with isolated data. See the [project overview](project-overview.md) for the boundaries of that run.

## Implemented with verification remaining

| Capability | Done when |
| --- | --- |
| Reviewed update API and administrator revision history | Present and verified on SQLite and disposable PostgreSQL 14, including stale and competing updates. The production container smoke check remains open. |
| Incident review UI | The three decisions, settings separation, and reload-and-compare recovery are implemented and core browser journeys passed. Remaining failure-path and accessibility checks are in the [numbered checklist](specs/incident-review/tasks.md). |
| AI discovery, verification, and Telegram outboxes | Isolated tests cover expected gates and delivery outcomes. Live AI quality, usage, and scheduling are separate milestones below. Any external delivery check requires an explicitly approved test setup. |

The review scope is agreed. The [specification](specs/incident-review/spec.md), [implementation plan](specs/incident-review/plan.md), and [numbered checklist](specs/incident-review/tasks.md) track the next milestone.

## Remaining work in order

| Milestone | Done when |
| --- | --- |
| Finish incident review and existing-incident updates | Every agreed acceptance criterion passes, including explicit target confirmation, source-only updates, duplicate rejection, revision history, recoverable drafts, reload and compare, settings separation, and accessible browser review. The release limits editable incident data to seven text fields. Existing public-map, import, and notification behavior passes regression checks. |
| Evaluate live AI under explicit authorization | Agree on a small sample, a budget, evaluation measures and thresholds first. Record reviewer-assessed relevance, source reliability, uncertainty and missed or duplicate reports within the sample, alongside actual usage and cost. Decide whether quality and cost support scheduling. |
| Prepare scheduled operation | Agree on cadence, limits, ownership, pause procedure, and failure signals. In an authorized environment, verify scheduled execution, pause and restart behavior, and review of the resulting run records before routine use. |

## Deferred scope

- Historical research and client-provided leads need their own scope, evidence criteria, and evaluation dataset.
- Advanced incident matching needs a defined error tolerance and examples of false matches and missed links. The immediate workflow uses human confirmation.
- Telegram notifications for material updates need a separate decision about eligibility, recipients, and deduplication. Reviewed updates remain silent in the proposed feature.
- Dates, coordinates, other classifications, and restoration from revisions are outside the first review release.
