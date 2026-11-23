# Incident review specification

**Status** — Scope agreed for implementation planning.

## Problem and outcome

The administration screen puts settings, evidence, relationship decisions, and a long incident form on one page. Reviewers should understand what a report claims, inspect its supporting evidence, and choose one clear action.

## Users, scope, and compatibility

Signed-in reviewers and administrators review RSS and AI reports from the queue or existing Telegram links. Public readers see approved incidents on the map and list. Keep account authentication and reviewer permissions, review links, resolved reports and feedback, incident IDs, workbook provenance, date and location precision, and existing Telegram behavior. Put AI settings, Telegram settings, and workbook import in a separate administration view. Eligibility and uncertainty follow the [domain rules](../../incident-selection-rules.md).

The requested live discovery pilot also delivers uncertain AI drafts to the configured
private editorial chat for inspection and explained rejection. This does not make them
eligible for subscriber alerts or Telegram acceptance; source and relationship checks
still govern publication. See [live monitoring verification](../../ai-monitor.md#local-live-pilot-23-november-2026).

The first release supports changes to title, summary, affected systems, impact, method, countermeasures, and additional information. Dates, coordinates, and other classifications are deferred. Historical research, advanced matching, material-update alerts, and restoration from revisions are out of scope.

## Review decisions

1. Open a report and see its title, summary, status, main source, uncertainty or conflicting claims, and any suggested match with its reason. Full evidence and excerpts are available on demand. Opening a report, source, or settings leaves publication and activation state unchanged.
2. **Publish a new incident.** Review the incident fields and references, then confirm two independent reporting origins and explicitly publish. A flagged report requires a separate-event decision and a reason before it can be published as new.
3. **Update an existing incident.** A reviewer can choose this for a pending unflagged report or one flagged as a possible update or duplicate. A suggestion can help find the target. The reviewer must select a published incident, compare current and proposed values, and explicitly confirm that target before saving. The backend must independently verify report eligibility, target publication status, target confirmation and review version, and the allowed changes and evidence.
4. **Reject the report.** Save the reason and written explanation using the existing rejection and learning workflow. Use the existing `duplicate` reason when the report repeats known information and adds no useful evidence. Keep the decision and evidence accessible.
5. After a save, show which action succeeded and which report or incident it affected. Refresh the queue and public data as applicable. If refresh fails after save, identify the saved outcome and offer to retry the refresh.

## Update behavior

- Require a reason and explicitly selected supporting sources. Factual updates require two independent reporting origins and reviewer confirmation. Existing sources may contribute to corroboration. One genuinely new supporting source is sufficient for an evidence-only update. Reject submissions that contain neither a changed value nor a genuinely new source. Preserve unknowns and distinguish attributed, disputed, and corroborated claims.
- Direct edits to published facts also require two independent citations and explicit
  source confirmation, including when the request retains the current sources. A
  no-op or hiding a record does not assert new facts. Canonical URL aliases cannot
  overwrite contradictory, unopened or differently attributed evidence.
- Change only the confirmed incident and selected fields or sources. Keep prior sources, provenance, and unselected data. Save the update, report outcome, and linked revision together. A repeated submission cannot create a second revision or alert. Updates create no new incident or publication alert. Before review, related reports may alert the configured private reviewer chat. They are excluded from public and subscriber alerts. New reports retain their existing alert behavior.
- Provide an administrator-only history view for the incident. Show when each revision was recorded, why it was made, and what changed, including previous and new values and added sources where applicable.

## Drafts, outdated records, and feedback

- Preserve unsaved editable text and reasons separately for each report and action during the current review session. When switching reports or actions, clear update comparison selections and target confirmation. Returning to an update requires loading and confirming the target again. Never carry drafts or selections between reports.
- When switching update targets, retain the report draft and reason. Clear the old comparison, selected fields and sources, and target confirmation. Load the new target before allowing further selection.
- If the target changed after it was opened, block the save and show **Reload and compare**. That action loads the latest target and report outcome, retains recoverable text and the reason, and clears selected fields, selected sources, and confirmation. Require a fresh comparison, selection, confirmation, and manual submission. If someone already resolved the report, show its saved outcome and disable writes.
- Distinguish loading, empty, error, saving, and success states. Explain when supporting evidence or an applicable change is missing. Retain recoverable input after validation and network errors. Prevent repeat submission while a request is pending.
- Provide labelled controls, visible focus, logical keyboard order, text labels for differences, and announcements for asynchronous status and errors. Move focus to the relevant heading or error after major transitions. Support narrow screens without horizontal scrolling.

## Acceptance criteria

| ID | Scenario | Observable result |
| --- | --- | --- |
| IR-01 | Open the queue or an existing direct link | Summary, key sources, uncertainty, and match reason are available before choosing an action. Settings remain separately reachable and opening them starts no external action. |
| IR-02 | Publish a new incident | Explicit approval creates one incident and follows existing notification rules. Flagged reports require a separate-event decision and reason first. |
| IR-03 | Apply a reviewed update | Pending unflagged and eligible flagged reports can update an explicitly confirmed published target. Missing confirmation, invalid targets, protected fields, and ineligible reports are rejected. Only the seven text fields or supporting sources can change. |
| IR-04 | Correct a fact or add evidence | A reasoned correction requires two independent supporting origins and reviewer confirmation. One useful new source can be added on its own. An unchanged value or already-recorded source alone produces no write. Existing data and sources survive. One revision and no alert are recorded for an accepted update. |
| IR-05 | Open revision history | An administrator can see when, why, and what changed, including source additions. An unauthenticated request cannot read history. |
| IR-06 | Switch report, action, or target | Draft text and reasons can be recovered in their original report and action. Old target selections and confirmation are cleared. Late responses cannot restore a previous selection. |
| IR-07 | Submit an outdated or repeated decision | A stale write leaves both records unchanged. **Reload and compare** requires a new review and manual submission. An already resolved report shows its outcome. Repetition creates no additional revision or alert. |
| IR-08 | Reject a duplicate | The existing `duplicate` reason and feedback appear on the resolved report. It leaves the pending queue and no public incident changes. Enter in the rejection form never triggers publication. |
| IR-09 | Exercise asynchronous states | Loading differs from empty and failed requests. Failed writes retain recoverable input. Save success is distinguishable from refresh failure, and refresh can be retried separately. |
| IR-10 | Review by keyboard and on a narrow screen | All decisions and the reload action are operable by keyboard. Focus is visible, labels and announcements are meaningful, and actions and comparisons fit without horizontal scrolling. |
