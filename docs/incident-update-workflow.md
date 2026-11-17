# Reviewed incident updates (Option A)

This feature is based on `feature-ai-research-improvements`. It closes the
`possible_update` review path without creating a duplicate incident.

## Intended reviewer workflow

1. AI/RSS identifies a candidate linked to a published incident as `possible_update`.
2. Reviewer opens that pending candidate, compares the original incident with the
   candidate and sources, and explicitly selects verified text changes.
3. Reviewer supplies a mandatory reason and optionally adds additional source URLs.
4. The server updates only approved fields of the *existing* incident, adds new
   sources without deleting existing ones, records the previous field values in
   `incident.raw_fields._updateReviews`, and records the reviewer decision in
   `candidate.evidence.relationshipReviews`.
5. The candidate becomes `approved`, pointing to the original `incident_id`.
   Its initial `possible_update` classification is retained as provenance.
6. Because this is not new publication, no publication event or Telegram delivery
   is created.

The backend does not automatically use `candidate.proposed_fields` as facts.
The reviewer must explicitly input verified changes.

## Endpoint

`POST /api/admin/candidates/{candidate_id}/apply-update`

Requires existing admin bearer token. Example:

```json
{
  "reason": "Confirmed impact details against original reports",
  "changes": {
    "impact": "Temporary disruption to port operations",
    "additionalInfo": "The follow-up report provides confirmed details"
  },
  "sourceUrls": ["https://example.invalid/new-confirmation"]
}
```

`changes` supports these text fields only: `description`, `summary`, `impact`,
`impactShort`, `method`, `affectedSystem`, `countermeasures`, `additionalInfo`,
`threatActor`, `victimType`, and `impactArea`. Both snake_case and camelCase
are accepted. If a field is omitted, the original value is preserved. Empty
replacement values are rejected to avoid accidental erasure. Up to 20 additional
HTTP(S) source URLs are permitted. If the review succeeds, the candidate's
original source URL is added to the existing incident's source list as well.

Requesting an update with no new field values or URLs returns 422. A candidate
that is rejected, approved, unlinked, or not `possible_update` returns 409.
Successful requests return `{ "candidate": ..., "incident": ... }`.

## Safety boundaries

- Publication status, title, year, dates, coordinates, source primary URL,
  and incident identifiers cannot be changed through this endpoint.
- Existing source links and stored import metadata remain untouched.
- No model retraining occurs; the recorded reviewer corrections may be used in
  future feedback/context experiments if deliberately integrated.
- The endpoint requires a human review; no AI-originated candidate is auto-applied.
- The original source link is preserved, and all changes are auditable.

## Local verification

In the root of the repository:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_apply_candidate_update.py -v
.\.venv\Scripts\python.exe -m pytest tests -v
```

In `frontend`:

```powershell
npm ci
node --test tests/update-client.test.mjs
npm test
npm run test:sites
npm run build
npm run dev
```

Start the backend against an isolated SQLite test database, with AI and Telegram
background work disabled. Do not point these tests at the client's production DB.
Use the pending `TEST ONLY - Material update to Port Alpha` fixture to validate
that the UI can apply a verified impact change to the linked original incident.
Expected: the original incident ID remains unchanged, incident count remains
constant, candidate becomes approved, and `_updateReviews` contains the record.

## Out of scope for this first version

Changing title/date/coordinates, editing a single related organisation/location,
merging source evidence by publisher, undo/rollback of applied reviews, and
outbound notifications about substantive updates require separate design and
validation. For now these changes are intentionally restricted to supported
text fields and sources.
