# Telegram review — Step 3: confirmed rejection

This is the first **write action** in Telegram. It reuses the same candidate fields and
conditional pending-to-rejected transition as the admin API. It never publishes an
incident or corrects its classification; both actions remain preview-only.

## Feature gates

Both `ENABLE_TELEGRAM_REVIEW_BUTTONS=true` **and**
`ENABLE_TELEGRAM_REJECTION_ACTIONS=true` are required for live rejection buttons.
`TELEGRAM_REVIEWER_IDS` must include the numeric Telegram **user** IDs of trusted
reviewers. All these settings default to disabled/empty.

**Keep both gates false on the deployed client bot.** First run the automated
suite and then conduct a controlled manual test using a different bot token,
an isolated database, and a real private Telegram chat. Never use production
candidate IDs or a production database for testing.

## Workflow

1. New AI candidates must be `pending`, `supported`, non-baseline, and unrelated
   to a known incident. The existing candidate alert outbox creates a delivery.
2. When the alert is successfully sent to a subscribed, authorized reviewer in
   their private chat, it has two preview buttons and an actionable **Reject**.
3. The reviewer selects Reject → one of five structured rejection reasons →
   **Confirm rejection** or **Cancel**. Nothing is changed before confirmation.
4. On confirmation, the backend checks all conditions again:
   - server review gates are enabled;
   - callback sender is an authorized Telegram user in their own private chat;
   - the reviewer is still subscribed and was actually delivered this candidate;
   - candidate is still pending, verified, and safely eligible;
   - atomic `status == pending` transition succeeds.
5. The candidate is marked `rejected`, `rejection_reason` is stored, and a compact
   audit entry with reviewer numeric user ID, reason, and time is appended to
   `candidate.evidence.telegramReviewHistory`. No public incident or
   publication notification is created.

**Scope limits:** This stage supports **structured rejection reasons only**.
Free-text explanations, Approve, and Correct classification are not implemented
in Telegram yet. Use the web admin panel for those actions. The `other` rejection
reason is intentionally excluded until a free-text note flow is available.

## Run automated tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_telegram_rejection_flow.py -v
.\.venv\Scripts\python.exe -m pytest tests -v
```

## Safe manual test (after all simulated tests pass)

In a local-only session configure a **separate** test bot and SQLite database.
Set reviewer IDs to your own Telegram user ID and subscribe to your private test
bot. Seed a synthetic supported AI candidate and send an eligible candidate
notification, using the same outbox path as the existing app. Verify Reject →
reason → Cancel leaves it pending. Send a second synthetic candidate, then test
Reject → reason → Confirm. Confirm the saved `rejectionReason`,
`telegramReviewHistory`, zero additional published records and unchanged
publication outbox. Repeated confirmation should not change the original reason.

Do not enable a real bot or background polling against the client deployment
before authorization, approval, notes, and concurrency behavior are reviewed.
