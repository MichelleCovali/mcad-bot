# Hosted MCAD service

The service combines the read-only public incident API, administrator writes,
the built frontend, an RSS review queue and durable Telegram delivery workers.
It does not run the legacy `bot.py` or `commands.py` loops.

## Configuration

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | PostgreSQL connection URL in production. Required on Railway; local default is `sqlite:///./data/mcad.sqlite3`. |
| `ADMIN_API_TOKEN` | Server-side secret protecting every write, candidate review and workbook upload. Without it administration returns HTTP 503. |
| `PUBLIC_BASE_URL` | Public HTTPS site URL used in `/map` and notification deep links. |
| `TELEGRAM_BOT_TOKEN` | Optional existing bot token. When absent, no Telegram network requests run. |
| `TELEGRAM_CHAT_ID` | Optional explicitly configured initial recipient. Startup never reverses an existing unsubscribe. |
| `ENABLE_BACKGROUND_WORKERS` | Defaults to `true`. Set `false` for isolated local/API testing. |
| `ENABLE_FEED_POLLING` | Defaults to `true`; fetches candidates every 15 minutes while workers are enabled. |
| `ENABLE_TELEGRAM_POLLING` | Defaults to `true`; requires a token and enabled workers. |
| `CORS_ORIGINS` | Optional comma-separated allowed frontend origins. Omit for same-origin hosting. |

Keep these server secrets outside frontend `VITE_` variables. No health response
or worker error log includes tokens, chat identifiers or database URLs.
Stop old Telegram command listeners before enabling hosted polling; Telegram
permits polling or a webhook, and competing readers of a shared bot interfere.

## Start and migrate

Run the versioned migration command before starting the single app worker:

```sh
python -m mcad migrate
uvicorn mcad.api:app --host 0.0.0.0 --port "$PORT" --workers 1
```

Migration 1 uses checked-in PostgreSQL/SQLite DDL snapshots. The
`schema_versions` table records applied versions. PostgreSQL migrations hold a
transaction advisory lock. Future schema changes must add a new migration and
append it to `mcad.migrations.MIGRATIONS`; do not regenerate or edit deployed
version 1 snapshots. Production migrations never infer alterations from model
changes. Back up the database before future migrations. Downgrades are not
implemented.

The app serves `frontend/dist/client`; build the frontend before deployment.
Migrations run explicitly, never as a side effect of importing the API module.

## API

Public reads:

- `GET /api/health` returns connectivity, feature status and aggregate counts.
- `GET /api/incidents` returns published records with camelCase fields and
  `meta: {total, mapped, unmapped, lastUpdated}`. Unknown coordinates remain
  null. No locations or dates are invented.
- `GET /api/incidents/{id}` also returns all original `rawFields`, sources,
  organisations, actors, locations and import provenance.

Administrator operations require `Authorization: Bearer <ADMIN_API_TOKEN>`:

- `POST /api/incidents` creates or idempotently updates a source record.
- `PATCH /api/incidents/{id}` edits a record. Normal edits do not resend alerts.
- `GET /api/admin/candidates` lists pending RSS review candidates; `status=all`
  or `status=approved` can inspect previous reviews.
- `POST /api/admin/candidates/{id}/approve` requires a human-verified title and
  accepts corrected incident fields; it publishes once. Repeated approval is
  idempotent.
- `POST /api/admin/import-workbook` accepts the raw XLSX body with
  `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` or
  `application/octet-stream`, at most 10 MiB. Authentication precedes reading
  the body. The temporary file is deleted after processing. Imports are silent.

Writes accept camelCase or snake_case fields. Title is required on creation;
latitude and longitude must be supplied together, both null or valid numbers.
Source links accept HTTP(S), never script/data URLs or embedded credentials.
The shared `upsert_incident(session, payload, external_id=None, notify=False,
import_batch_id=None, source_row=None)` function does not commit. Importers own
their transaction. `notify=False` suppresses publication events for historical
records, including a later status change.

External IDs identify source rows. Distinct external IDs remain separate even
when they cite one article. Only requests without an external ID use normalized
source URL fallback. Original reference numbers are indexed, not unique. Each
imported source row retains batch, row number and raw fields separately.

## Notifications and review

RSS filtering creates candidates, never map incidents. The first poll marks a
baseline; neither baseline nor subsequent feed collection sends notifications
or automatically geocodes/approves a report.

Private `/subscribe` opts into future verified publication events;
`/unsubscribe` stops delivery. `/start` and `/help` explain commands. `/latest`
reads the newest published database record; `/map` opens the hosted map. The
polling offset and processed update IDs survive restarts. A command replay does
not repeat its effects or reply.

Publication and its outbox event commit atomically. Deliveries are unique per
event/recipient and snapshot currently active subscribers; subscription never
backfills historical events. Workers use DB leases plus atomic delivery claims
to prevent parallel sending during overlapping deployments. HTTP 429 persists
Telegram's `retry_after`; transient failures use backoff. A blocked bot deactivates
that subscriber. Telegram replies and alerts use plain text without HTML parsing.

Telegram does not provide a general send-message idempotency key: a crash after
remote delivery and before recording success can produce a duplicate when an
expired lease retries. This outbox therefore provides durable at-least-once
delivery, not an impossible exactly-once guarantee. Command offsets commit
before sending replies to avoid replay spam; a crash in that small interval can
lose the command reply, and the user can issue the command again.

## Tests

```sh
python -m pytest tests/test_backend.py tests/test_import_workbook.py
```

Tests use temporary SQLite databases, synthetic notifications and injected feed
data. They never call Telegram or start production pollers.
