# Telegram classification corrections (Step 6)

This is an **opt-in**, private-chat review feature for AI candidates that have already been sent to an authorized reviewer. It does **not** publish incidents or train a model. It adjusts only the pending candidate's proposed classification; published incidents remain unchanged until a separate, explicit approval.

## Configuration

All flags default to `false`. To test in a dedicated bot/database environment, configure:

```dotenv
TELEGRAM_REVIEWER_IDS=123456789
ENABLE_TELEGRAM_REVIEW_BUTTONS=true
ENABLE_TELEGRAM_CLASSIFICATION_ACTIONS=true
```

Keep the production settings disabled until integration tests using a dedicated test Telegram bot and isolated SQLite database have succeeded. The existing approval and rejection flags are separate and can remain `false`.

## Review procedure

1. AI creates a **supported**, **pending**, **new or unclassified** candidate. The system sends a notification to subscribers.
2. Only an authorized reviewer in a private chat receives the active **Correct classification** button.
3. Reviewer selects a field: maritime category (`victimType`) or incident method (`method`).
4. Reviewer selects a predefined replacement value and sees the old and proposed values.
5. Reviewer explicitly confirms within 15 minutes; the candidate's `proposed_fields` is updated while `status` remains `pending`.
6. The system appends an audit record to `candidate.evidence.classificationReviews`, including field, old/new values, reviewer Telegram ID and UTC time.
7. The reviewer must **separately approve** or reject the candidate. Any earlier Telegram publication confirmation is invalidated when candidate fields change. Approval still requires the usual evidence and source checks.

Current choices are intentionally limited:

- Category: Vessel; Port; Offshore infrastructure; Undersea infrastructure; Other maritime infrastructure
- Method: AIS spoofing; GPS spoofing; GPS jamming; Ransomware; Other / undetermined

For more specialized classifications, incident identity changes, source corrections, or geographic edits, use the web admin panel. This restricted menu is not a comprehensive maritime taxonomy and should be reviewed with the client before enabling live actions.

## Security and data integrity

- Check `callback_query.from.id`, not the chat ID alone; require a matching private chat and allowlisted reviewer.
- Verify the candidate's alert was actually delivered to the reviewer's active subscription.
- Reject edits when the candidate is no longer pending, is a baseline, has another origin, is not supported, or may be a duplicate/update linked to another incident.
- Require a server-side, expiring draft plus a fresh unpredictable confirmation nonce. Duplicate button taps cannot apply the same action twice.
- Reject stale updates when another reviewer or the web interface has already changed the selected field.
- Record the correction without creating `Incident` or `OutboxEvent` objects and without sending publication notifications.
- The approval confirmation's stored field fingerprint makes an earlier confirmation invalid when fields change.

## Local test commands

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_telegram_classification_flow.py -v
.\.venv\Scripts\python.exe -m pytest tests -v
```

The Python suite uses simulated Telegram callback events. It does not establish live delivery. For live testing, use a separate bot, allowlisted test-user ID, explicit feature switches, and isolated test incidents. Never use a production database for fictional test reports.

## Limitations and next improvements

- Classification corrections are kept in the candidate and its review audit log; they are **not yet passed to future AI prompts** as learning examples. The AI monitor currently uses rejected-candidate feedback.
- Only predefined options are supported; free-text classification corrections and optional review reasons are future enhancements.
- The correction pathway does not guarantee that the AI's claimed independent sources are actually independent. Human verification remains necessary before publication.
- No live Telegram integration test has been completed by this patch alone.
