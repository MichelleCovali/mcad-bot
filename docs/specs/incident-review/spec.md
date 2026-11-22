# Incident review specification

**Status** — Draft for review. Implementation planning starts after agreement.

## Problem and outcome

The administration screen puts settings, evidence, relationship decisions, and a long incident form in one page. A reviewer should be able to understand what the report claims, what supports it, and what is uncertain before choosing **Publish a new incident**, **Update an existing incident**, or **Reject the report**.

## Users, scope, and compatibility

Administrators review incoming RSS and AI candidates and may open a candidate from an existing Telegram link. People browsing the public map or list see approved records and their sources. This feature covers the review queue, the three decision journeys, and separating routine review from administration settings.

Preserve administrator authentication, existing review links, access to resolved reports and saved feedback, original workbook fields, date and location precision, published incident identifiers, and existing Telegram behavior. Keep workbook import, AI controls, and Telegram controls available through a separate administration view. Use the [domain rules](../../incident-selection-rules.md) for eligibility, uncertainty, and the distinction between a new incident and an update. Historical research, automated entity matching, and alerts for material updates are deferred.

## Proposed review flow

1. Open the queue or a review link. At the top of the report, show its title, summary, status, main source, uncertainty or conflicting claims, and any suggested incident match with its reason. Keep full evidence, excerpts, and secondary metadata reachable on demand.
2. Choose one decision. Show the fields and controls for that decision. Before saving, let the reviewer return to the report summary or choose another action.
3. **Publish a new incident.** Review the relevant incident fields and reference links. For a flagged relationship, explicitly resolve it as a separate event and record why before publication. Require an explicit publication action.
4. **Update an existing incident.** Suggest a match when available and require the reviewer to explicitly confirm the published target. Show current and proposed values alongside each other where space permits, or in clearly labelled pairs on narrow screens. Require selection of changed fields or new evidence, at least one supporting source, and a reason. Show the target and selected changes before the reviewer applies them.
5. **Reject the report.** Show the rejection reason and feedback inputs. Save the decision and keep the evidence and feedback accessible. Return to the queue or show the saved outcome in the linked report.
6. After any successful decision, identify the action and affected report or incident. Refresh affected queue and public records. If a refresh fails after a save, identify the saved result and offer to retry the refresh.

## Functional requirements and states

- Keep unverifiable fields unknown and label attributed, disputed, or uncertain claims clearly. Opening a report, a source, or settings must leave publication state and AI activation unchanged.
- Update only the selected incident. Retain previously stored sources and unselected data. Store a linked revision with the reason, review evidence, and before/after values. A repeated submission must leave a single successful revision and existing incident identifier. Use current review eligibility rules pending the decision on unflagged reports below.
- When a target changes after review begins, explain the conflict, keep the report available, reload the latest target, and require a fresh comparison and confirmation. When the report is already resolved, show its stored outcome and disable further writes.
- Show distinct loading and empty states for the queue and target search. A report with no available supporting source or applicable change must explain what is missing. Keep the report and recoverable input available after a validation or network error. Prevent repeat submissions while a decision is saving.
- Use labelled controls, logical keyboard order, visible focus, and text labels for differences and status. Support keyboard operation across all decisions, move focus to the relevant heading or error when the view changes, and announce asynchronous status and errors. At narrow widths, keep content readable and actions reachable without horizontal scrolling.

## Acceptance criteria

| Scenario | Observable result |
| --- | --- |
| Open a pending report from the queue or a direct link | The reviewer can read the summary, key sources, uncertainty, and any match reason before opening the action-specific controls. Settings remain reachable in their own view. |
| Publish a new incident | An unflagged report can be published after explicit confirmation. A flagged report requires the separate-event decision and a reason first. A successful save creates one public incident and follows existing publication delivery rules. |
| Apply a reviewed update | A suggested match alone cannot enable submission. After confirmation of a published target, selection of changes and supporting sources, and entry of a reason, only that target changes. Earlier sources and unselected fields remain, one linked revision is stored, and no Telegram alert is queued. Switching targets requires reviewing the new comparison. |
| Submit a stale or already-completed review | A stale target leaves the incident and report unchanged and offers a fresh comparison. An already-completed review shows its outcome. Repeated submission creates no additional revision or alert. |
| Reject a report | The saved report shows its feedback and leaves the pending queue. No public incident is created or modified. Pressing Enter in rejection controls cannot trigger publication. |
| Exercise loading, empty, and failure cases | A slow request displays a loading state. A successful empty result differs from an error. Failed writes show how to retry and preserve recoverable inputs. Success followed by refresh failure reports the saved decision separately. |
| Review with keyboard, screen reader, and narrow viewport | Each decision can be completed with keyboard controls. Focus is visible, controls have meaningful names, status changes are announced, and comparisons and actions remain usable on a narrow screen. |

## Decisions to confirm

- **Update eligibility.** The existing update API accepts reports flagged as possible updates or duplicates. Should reviewers also be able to identify an unflagged RSS or AI report as an update during review?
- **Editable fields and source-only changes.** The local comparison covers title, summary, affected systems, impact, method, countermeasures, and additional information. Which fields belong in the first release? Should genuinely new evidence alone qualify, and should changes to date, location, or confirmation status be supported now?
- **Source selection and rejection.** The draft requires at least one selected supporting source and an update reason. Confirm whether already-recorded sources may support a changed fact, and whether a duplicate with no useful new evidence should use a dedicated rejection reason.
- **Revision visibility.** Is stored history sufficient for the first release, or should reviewers be able to open previous revisions in administration?
- **Administration and reviewers.** The draft assumes the existing administrator role. Confirm which controls should appear together in the settings view and whether any review access needs a separate role.
