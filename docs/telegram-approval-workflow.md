# Telegram interactive review — Step 4: confirmed publication

**Status:** implemented and tested with *simulated Telegram updates*. Production
operation and end-to-end live-bot delivery have **not** been verified.

This extends Step 3 (structured rejection) with a separately gated **Approve**
action. **Correct classification** and rejection notes still require the web
administrator panel. No database migrations are needed.

## Feature gates (off by default)

```dotenv
TELEGRAM_REVIEWER_IDS=123456789
ENABLE_TELEGRAM_REVIEW_BUTTONS=false
ENABLE_TELEGRAM_REJECTION_ACTIONS=false
ENABLE_TELEGRAM_REJECTION_NOTES=false
ENABLE_TELEGRAM_APPROVAL_ACTIONS=false
```

- Set reviewer **user IDs**, not usernames or group chat IDs. No IDs means
  nobody may review in Telegram.
- `ENABLE_TELEGRAM_APPROVAL_ACTIONS=true` only has an effect when review
  buttons are also enabled, a nonempty bot token exists, and an authorized,
  subscribed reviewer has been sent this specific AI candidate.
- Keep **all four switches false on the client bot** until the team approves a
  staged deployment and completes the live tests using a separate bot/database.
- Do not enable Telegram polling simultaneously in two deployments using the
  same bot token. Set `ENABLE_BACKGROUND_WORKERS=false` locally when using
  production-like data.

## Review flow

1. The AI monitor creates a **pending**, `supported` AI candidate with a fresh
   research report and multiple opened, supporting source origins.
2. The candidate-notification worker sends it to subscribers. Only authorized
   reviewers in their own private chats receive interactive review buttons.
3. **Approve (review details)** displays the proposed title, year, victim
   category, method, summary and supporting source URLs. Nothing is published.
4. The reviewer must check the displayed sources and data, then press
   **Confirm PUBLIC publication** or **Cancel**. Confirmation data is signed
   with the bot secret, bound to their user ID and expires after 15 minutes. The pending confirmation is saved
   server-side and revoked on Cancel or successful publication.
5. A confirmed action rechecks that the user is authorized/subscribed and
   received the candidate; the candidate is still pending, supported, from AI,
   not baseline, and not possibly an update/duplicate; and at least two distinct
   hosts and source origin groups were opened with supporting evidence.
6. If the candidate matches an existing incident by source URL, source link, or
   exact title/year, Telegram does **not** publish it. Use the web admin panel.
7. A conditional pending-to-approved transition creates **one** published
   incident from an explicit field allowlist. The candidate links to that
   incident, AI evidence and reviewer ID are retained, and a normal publication
   outbox event is created. Subscribers who already received the candidate
   report are excluded from receiving it twice.

**Important:** Source independence and accuracy remain **human judgments**.
The automatic checks can establish only what the AI recorded; different
publishers can still copy the same original report. Telegram confirmation is
not a substitute for opening sources and checking classifications. Geographical
coordinates, curator reference numbers, external IDs and other sensitive
fields are not accepted from AI through this approval route. Use web admin
review when any correction is needed.

## Local simulated validation

Run from the project root:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_telegram_approval_flow.py -v
.\.venv\Scripts\python.exe -m pytest tests -v
```

Tests cover two-tap approval, source preview, valid/expired/forged callbacks,
unauthorized users, unsubscription, candidates no longer pending, source
corroboration requirements, unsafe proposed fields, existing incident matches,
exactly one publication, review history, and notification suppression. External
Telegram Bot API requests are **not** made by these tests.

## Staged manual acceptance test (not yet performed)

Use **a separate Telegram bot token** and a **fresh isolated SQLite database**.
Seed one synthetic `supported` AI candidate with two fictional *validly shaped*
source evidence entries. Subscribe to this test bot from your own private
Telegram account. Enable the review/approval switches only in that test server.
Confirm that Approve displays the detailed review screen, Cancel leaves the
record unchanged, and Confirm publishes exactly one record. Re-click Confirm
and verify no new record appears. Retry as an unauthorized Telegram user and
with a candidate marked `possible_update`; both should be denied. Finally
inspect the candidate `telegramReviewHistory`, published incident `rawFields`,
and outbox records. Turn the switches **off** afterward.

**Never use synthetic incidents with a production bot or production database.**
