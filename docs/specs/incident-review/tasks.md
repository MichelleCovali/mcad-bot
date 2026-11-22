# Incident review progress checklist

**Implemented** records existing code. **Verified** records a completed check and its scope. **Remaining** identifies work or checks still to do.

1. **Establish the baseline**
   1. **Implemented.** The branch has review endpoints, relationship guards, revision storage, and duplicate rejection. The working tree has an update comparison draft and API client changes.
   2. **Verified.** The isolated baseline passed 89 backend and 36 frontend tests with the local frontend work included. Seven opt-in backend checks were skipped. See the [baseline](../../project-overview.md).
   3. [ ] **Remaining.** Compare the existing frontend draft and callers with the agreed flow. Record which components and API behavior can be reused before editing.

2. **Make updates safe on the backend** (after 1.3)
   1. [ ] Support pending unclassified and eligible flagged reports with explicit version-bound confirmation of a published target. Enforce the seven text fields, source validation, and reason. Verify **IR-03** with authenticated API cases for both eligible report types, missing confirmation, invalid targets, invalid states, and disallowed fields.
   2. [ ] Preserve evidence and prevent no-ops, stale writes, and concurrent duplicate decisions. Verify **IR-04, IR-07** with service and API tests for a fact correction backed by an existing source, source-only success, no-op rejection, repeat submission, and rollback. On disposable PostgreSQL test competing updates to one incident and competing decisions on one report. Check prior sources, one revision per accepted update, and unchanged notification outboxes.

3. **Expose revision history** (after 2.2)
   1. [ ] Add the administrator read endpoint and history panel using existing snapshots. Verify **IR-05** with authorization tests, fixtures for existing and empty history, field diffs, and a source-only revision. Check the panel shows recorded time and reason and returns to the same incident.

4. **Build the review journeys** (after 2.1 for update integration)
   1. [ ] Separate the queue and settings, preserve deep links, and reuse publish and rejection behavior. Verify **IR-01, IR-02, IR-08** with a fixture-based browser walkthrough and regression tests for auth, separate-event approval, rejection with Enter, and mocked delivery rules. Opening settings must make no activation or delivery request.
   2. [ ] Wire comparison, target confirmation, draft retention, and reload-and-compare recovery to the API. Verify **IR-03, IR-04, IR-06, IR-07, IR-09** with fixture journeys for switching report, action, and target, out-of-order responses, correction and source-only updates, stale or resolved records, validation and network failures, and retry of refresh after a successful save.

5. **Verify readiness** (after 2–4)
   1. [ ] Verify **IR-10** in the browser with keyboard and screen reader on wide and narrow layouts. Record focus movement, labels, announcements, and access to all decisions and conflict recovery.
   2. [ ] Run the [automated checks](../../ci.md), including disposable PostgreSQL migration and update checks, frontend build, Sites output, and container smoke test. Confirm existing workbook import, public map and links, and notification behavior with synthetic fixtures.
   3. [ ] Record the result of every acceptance criterion with its test or walkthrough. Mark the milestone done only after required checks pass. Record rollout compatibility and any separate authorization needed for external operations.
