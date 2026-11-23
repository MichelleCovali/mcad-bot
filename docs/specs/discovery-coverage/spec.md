# Discovery coverage and corroboration

Status: agreed implementation scope, 23 November 2026. The user requested finishing
the current Responses pipeline before testing Agents API, multiple independent sources
for confirmed news, open-web discovery without chosen sites, and checks for daily gaps.
The existing archive defines the scope: maritime cybersecurity and electronic navigation
interference, including affected vessels, ports, maritime organisations and offshore
systems. Ordinary fires, collisions and piracy without this component remain outside it.

## Behavior

- Perform a primary discovery and a complementary gap search, even if the primary
  search is empty. Use different maritime categories, regions and local-language
  queries, without a publisher whitelist. Search priorities supplement this scope.
- Share the existing per-run tool budget across both passes and verification. A budget
  too small for both passes is visibly partial. Activation, daily limits, pause checks,
  and a single run lease remain in force before every paid request.
- Combine and deduplicate source-grounded leads before verification. Expose candidate
  truncation and deferred checks. Keep a bounded, cooled-down followup of unresolved
  recent drafts, refreshing the original draft without duplicate notifications or
  overwriting an intervening human decision.
- Seek at least two independent reporting origins and open their evidence. Copies of
  the same statement count once. One-source leads remain unconfirmed. Publication and
  changes to published facts require independent-source confirmation; adding a citation
  alone must not imply newly confirmed facts.
- Record actual windows, returned search queries, pass outcomes, unresolved verification
  and limitations. Daily reporting explicitly uses UTC, matching existing quotas.
  A completed search is an attempt, never a guarantee that every world event was found.
- Preserve accounts, review workflows, incident update matching, historical import,
  source provenance and opt-in Telegram behavior. Agents API is deferred.

## Verification

Synthetic tests must demonstrate an empty first pass rescued by the second, grounding
and cross-pass deduplication, capped/deferred leads, shared budgets, failure and pause
boundaries, honest UTC reporting, retained usage on errors, followup identity and
concurrent reviewer protection, and minimum-source publication gates. Run existing
backend, PostgreSQL, frontend and container checks. Use only separate test credentials
for an explicitly bounded local live check; retain missing provider usage as unknown.
