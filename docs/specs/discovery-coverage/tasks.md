# Discovery coverage tasks

- [x] Add and verify legacy coverage migration on disposable SQLite histories.
- [x] Implement bounded complementary discovery and actual query audit.
- [x] Verify empty-first rescue, deduplication, limits, failures and pause boundaries.
- [x] Implement and verify unresolved-draft followup without duplicate alerts or lost decisions.
- [x] Close confirmed-fact publication bypasses with regression tests.
- [x] Display UTC daily checks, pass detail and unresolved verification without completeness claims.
- [x] Run 198 backend tests, 46 frontend tests, four Sites checks and the production frontend build; inspect the local browser.
- [x] Record the bounded live result and its incomplete verification explicitly below.
- [x] Verify PostgreSQL and container CI and update the feature PR. Merge follows successful branch checks.

CI on `bd10e81` passed all four jobs: 198 backend tests, eight PostgreSQL checks,
46 frontend tests, four Sites checks and production container readiness/authentication.
The local browser displayed actual query traces, replay markers, six deferred checks,
three unconfirmed drafts and no source-supported/publication claim. Private bot delivery
state recorded three sent review notifications. [CI run](https://github.com/MichelleCovali/mcad-bot/actions/runs/45216593507).

## Live verification

Two live discovery passes returned 20 query strings across multiple languages and seven
leads. The first five-report verification exhausted its 9,000 output tokens; it failed
closed and preserved the queue. Batches now reserve room for independent-source work
and proportionate output. A single paid verification retry reused the saved discovery
without new search calls, completed with three selected leads, and created one uncertain
draft. It has one qualified origin, remains unpublished, and reached the private test
review chat without an acceptance action. Six leads remain deferred. The retry is
explicitly marked as reused discovery and is not counted as a new completed sweep.

The failed live attempt estimated $0.509401; the additional verification estimated
$0.162300. The fourth UTC-day attempt was explicitly counted with a temporary test-only
four-run allowance, restored to three immediately afterward. The finite local pilot
continues at three per UTC day with the original cutoff. No Agents API was configured.
