# Telegram classification corrections on the operations branch

This opt-in extension builds on the existing **internal Telegram review chat**.
It does not replace Accept / Apply update / Reject, the two-source publication
checks, or the web-based review process. A correction changes only a pending
AI candidate's proposed classification; it does **not** publish an incident.

## Configuration

Keep `ENABLE_TELEGRAM_CLASSIFICATION_ACTIONS=false` in production until the
client has approved the choice list and a live test has succeeded. For an
isolated test bot and test database, configure:

```dotenv
TELEGRAM_BOT_TOKEN=<test bot token>
TELEGRAM_REVIEW_CHAT_ID=<numeric test review chat ID>
TELEGRAM_REVIEWER_IDS=<numeric authorized user ID>
ENABLE_BACKGROUND_WORKERS=true
ENABLE_TELEGRAM_POLLING=true
ENABLE_TELEGRAM_CLASSIFICATION_ACTIONS=true
```

Use an isolated database and fictional candidates. Never run fictional
records against the public MCAD site. Stop any other long-polling process for
the same test bot before testing.

## Review procedure

1. A **supported, pending, new AI report** is delivered to the configured
   Telegram review chat. Existing incident updates do not offer this action.
2. An allowlisted reviewer selects **Correct classification**.
3. The reviewer selects **Maritime category** (`victimType`) or
   **Incident method** (`method`), then one predefined replacement value.
4. The bot displays the previous and proposed value and requires an explicit
   **Save correction (no publication)** confirmation within 15 minutes.
5. The system saves the revised field in `Candidate.proposed_fields` and
   appends an entry to `Candidate.evidence.classificationReviews` with the
   reviewer Telegram user ID, old and new values, field and UTC time.
6. The candidate remains **pending**. The reviewer must separately approve
   or reject it using the existing workflow.

The options are deliberately limited, pending client approval:

- Maritime category: Vessel; Port; Offshore infrastructure; Undersea
  infrastructure; Other maritime infrastructure.
- Incident method: AIS spoofing; GPS spoofing; GPS jamming; Ransomware;
  Other / undetermined.

The bot edits only the inline buttons on the original review message. In a
group review chat, different authorized users share that visible menu;
confirmation is still bound to the individual reviewer. A private review chat
is preferable when only one person reviews reports.

## Safeguards and compatibility

- Authorization checks the actual Telegram user ID, configured review chat,
  delivered message ID, and delivery UUID on **every** callback.
- The correction feature is disabled by default. Forged callbacks cannot
  enable it.
- Confirmation uses a random 8-character nonce and an expiring server-side
  state record bound to the reviewer, candidate, delivery, and chat.
- Changed or no-longer-pending drafts cannot be corrected. Duplicate taps
  cannot append duplicate audit records.
- Corrections never create incidents or publication notifications.
- Source independence and publication requirements remain enforced by the
  new operations branch's existing `editorial.approve_candidate` pathway.
- No schema migration was added: the updated values and audit history use
  the existing `proposed_fields`, `evidence`, and `worker_state` database fields.
  The old feature branch's conflicting `v0005_candidate_relationships`
  migration must **not** be copied into this integration branch.

## Automated tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_telegram_classification_integration.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

These use isolated SQLite databases and synthetic Telegram callbacks, not a
live bot. Test authorized and unauthorized callbacks, confirmation, expiry,
stale edits, non-publication, and subsequent separate approval.

## Known limitations

- Corrections are audited but **not yet converted into AI prompt-learning
  cases**. The existing AI feedback learning pipeline processes rejections.
- The fixed Telegram menu is not a comprehensive maritime taxonomy.
- There is no standalone live Telegram end-to-end test in the automated suite.
- The operations branch's existing Accept action publishes directly after its
  own source checks. A separate two-step approval confirmation can be
  considered later if agreed with the client.
