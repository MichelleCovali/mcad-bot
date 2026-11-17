# Telegram review — Step 5: optional free-text rejection explanations

This stage extends the existing structured Telegram rejection process. When an
authorized reviewer selects a rejection reason, they can now **Add explanation**
(or **Confirm without explanation**).

## Feature switches

```dotenv
TELEGRAM_REVIEWER_IDS=
ENABLE_TELEGRAM_REVIEW_BUTTONS=false
ENABLE_TELEGRAM_REJECTION_ACTIONS=false
ENABLE_TELEGRAM_REJECTION_NOTES=false
ENABLE_TELEGRAM_APPROVAL_ACTIONS=false
```

All switches are **off by default**. Rejection notes are available only when
review buttons, rejection actions, and rejection notes are all enabled and the
reviewer's user ID is allowlisted. Keep the switches **off for production**
until a separate test bot, private reviewer chat, isolated database and full
review flow have been tested. Never use production candidate IDs for these tests.

## Reviewer flow

1. A subscribed, authorized reviewer receives an eligible AI candidate in a
   private Telegram chat. Other subscribers get a normal notification without
   interactive review buttons.
2. Tap **Reject** and choose one of the existing five structured reasons.
3. Tap **Add explanation**. The bot sends a ForceReply prompt. **Reply directly
   to that prompt** with a text explanation of at most 1,000 characters.
4. The bot stores the text temporarily as a short-lived draft, then sends a
   **Confirm with explanation** / **Cancel** keyboard. Nothing has changed in
   the candidate record yet. Drafts expire after 15 minutes.
5. On confirmation, the system rechecks that the reviewer is allowlisted, the
   candidate notification was delivered to their active subscription, and the
   candidate is still pending, AI-origin, supported and not linked to an
   existing incident. It atomically marks the candidate **rejected** and saves
   both the structured `rejection_reason` and free text in `rejection_notes`.
6. A compact `telegramReviewHistory` entry records reviewer ID, time, reason
   and whether an explanation was provided. No public incident or publication
   notification is created.

Clicking **Confirm without explanation** saves just the reason. **Cancel** or
`/cancel` (while writing a note) discards a draft. Selecting a new rejection
reason or beginning a new rejection also discards an older draft. A stale
**Confirm with explanation** button cannot apply an expired or missing draft.

The AI monitor already retrieves the `rejection_notes` field when preparing
future feedback prompts, so it can incorporate these explanations without
retraining model weights. Only recent selected feedback is included in a given
prompt; changes in future model behavior are not guaranteed.

## Local validation

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_telegram_rejection_notes.py -v
.\.venv\Scripts\python.exe -m pytest tests -v
```

The automated tests simulate Telegram messages and **do not** send real
messages, invoke the AI API, or verify live Telegram integration. Before a
staged deployment, test ForceReply text input, note expiration, cancellation,
retries, and conflict with web reviews using a separate bot and test database.
