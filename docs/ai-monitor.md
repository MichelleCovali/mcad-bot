# AI incident monitoring

The monitor is implemented but **paused by default**. Adding `OPENAI_API_KEY`, deploying,
opening the administrator panel, importing Excel, and saving a schedule do not start a search.
Automated checks use synthetic clients. An explicitly requested local live pilot is
documented separately below; adding credentials still does not activate monitoring.

## Configuration and activation

Keep `ENABLE_AI_SEARCH=false` in Railway while preparing the application. Keep the OpenAI
key in the backend service variables; it is never returned by the API or bundled into the
frontend. The administrator panel reports only whether a key is configured.

There are two independent switches:

1. The server environment variable `ENABLE_AI_SEARCH` defaults to `false`.
2. The saved administrator setting `enabled` also defaults to `false`.

Both switches and a configured key are required for any paid search, including a manual
run. The server blocks enabling the saved switch while its environment switch is off.
Later activation must be intentional: enable the server switch, then enable monitoring
in the administrator settings. A background schedule additionally requires
`ENABLE_BACKGROUND_WORKERS=true`. A manual run does not require background workers.
Pausing in the panel prevents new runs and is checked again immediately before each paid
stage and before candidate storage. It cannot cancel a provider request already in flight.

Settings allow **3, 4, or 6 runs per day** (intervals of 8, 6, or 4 hours), a reporting lookback
of 1–168 hours, 1–10 candidate proposals, and 2–30 web tool calls per run. The search
window always includes the current UTC day, even when the configured lookback is shorter.
Defaults are
3 runs/day, a 48-hour lookback, 5 candidates, and 15 tool calls. The schedule begins when
enabled and due; it is interval based rather than tied to local wall-clock hours.
Daily quotas use UTC and include manual, failed, and interrupted attempts because those
attempts may already have incurred usage. A database lease prevents overlapping workers
or manual runs. There are no automatic provider retries.

Administrator endpoints (administrator session or optional maintenance bearer credential):

- `GET /api/admin/ai-monitor`: settings, readiness and recent runs.
- `PATCH /api/admin/ai-monitor`: save settings; this request does not run a search.
- `POST /api/admin/ai-monitor/runs`: run once only after both activation switches pass.

## Research and review

The default discovery model is `gpt-6-luna`; the verification model is `gpt-6.1-sol`.
They can be configured server-side through `AI_DISCOVERY_MODEL` and
`AI_VERIFICATION_MODEL`. Availability depends on the OpenAI project's model access.

Discovery searches the open web without a fixed publisher whitelist. It seeks actual
maritime cybersecurity events, including port, vessel, shipping, offshore and navigation
system incidents. The verifier receives the leads, searches independently, and opens the
original evidence. It is instructed to distinguish original reporting from syndication,
allegations from confirmation, and newly reported incidents from old stories republished
today. Limited recent database titles and rejection feedback help avoid repeats and
known classification mistakes. Pending review reports also form bounded context to
avoid rediscovering the same draft. Discovery can submit a named one-source lead;
the separate verification stage seeks independent corroboration. Retrieved pages and stored feedback are treated as
untrusted content, not instructions.

Discovery runs a primary search and a complementary search for omissions. The second
pass considers the full window even when the first returns nothing, changes query angles
and local-language terms, and covers maritime categories and regions without chosen
publishers. Search priorities supplement this scope. Discovery prioritises the overlap
around the last successful run and still considers the full reporting window, so
late-indexed sources can qualify. Only leads grounded in
the discovery tool's sources reach verification. Exact repeats are screened before spending
on the second model; a fully empty verification result can be reused for six hours for the
same lead and configuration. A changed lead or new evidence can be verified again.
Candidate limits are shared across passes before verification. Deferred grounded leads
are carried in a bounded run backlog. Verification batches also reserve capacity for
a search and two opened sources per report; with the default allowance this selects
up to three reports per batch. Selected leads omitted from a valid response stay in
the backlog. Larger batches receive a proportionate output allowance, capped at 18,000
tokens. Recent unresolved drafts are rechecked after a
six-hour cooldown, with the least recently checked selected first. Successful checks
refresh the same untouched draft, retain earlier evidence and do not send another alert.
An intervening human decision is preserved. Ambiguous followup results are quarantined
in run evidence rather than creating another draft.

Structured results are checked locally before storage. Each evidence URL must appear
in the verifier's actual tool results or citation trail; excerpts and supported or
contradicted claims are retained. An assessment of `supported` requires two opened source
hosts with different reported origin groups and a usable first-reporting date. A copied
story from the same origin, missing freshness, or search snippets alone remains
`needs_review`. Conflicting claims are labelled `contradicted`. Known old reports are
excluded. Source independence and quoted claims still require human judgement: an
opened URL proves the research trail, not that every model claim is correct.
Each report is validated separately: one invalid report does not discard valid reports
in the same response. Excluded reports produce a batch warning on retained evidence.
English month names are normalized without inventing day-level precision. A completed
response may contain a dangling in-progress tool item; it contributes no accepted
sources or completed tool calls. Failed or incomplete overall responses still fail.

Every result is a pending candidate. A configured private editorial chat receives
pending AI drafts, including `needs_review` and `contradicted` reports, clearly marked
for further verification. These drafts have no Telegram acceptance action. The editor
can inspect the evidence in MCAD or reply with `/reject` and an explanation. Subscriber
research alerts remain restricted to supported new reports; possible updates and
duplicates never alert subscribers. Publication in either surface requires at least
two independent reporting origins.
Explained rejections become confirmed regression cases for the prompt workflow.
See [the operations guide](operations.md) for the review chat configuration.

Candidate proposals follow the Excel incident structure. Unknown facts remain empty or
`null`; dates keep their source precision, coordinates are not guessed, and the AI cannot
assign curator initials or internal reference numbers. Original source excerpts,
contradictions, uncertainties, reporting dates, the research time and run ID are retained.
Publication records the reviewed proposal and evidence snapshot. Existing Excel import
behaviour and the current Telegram message format are unchanged.

Discovery distinguishes a new incident from a fresh development in an older incident.
An update must contain new facts corroborated by two independent sources. Reference
overlap and matching incident details can suggest a target. A reviewer resolves
uncertain matches. Approved updates keep the original record and extend its published
history and references. Reposted facts stay out of the review queue. Known analytics
URL parameters are ignored when comparing sources.

Research alerts use their own durable outbox and per-recipient delivery state. Temporary
Telegram failures retry with backoff; opt-outs and reviewed reports cancel unsent research
alerts. At publication, people who already received the research alert are excluded from
the publication alert. Other active subscribers can still receive publication. Sending
and approval lock the same candidate so they cannot normally race into two messages;
the existing crash-after-send limitation remains because Telegram has no idempotency key.

The administrator's Telegram panel can send a labelled connection test to the configured
test chat, at most once per minute. It neither runs AI nor creates an incident. Automated
checks mock both external providers and also exercise PostgreSQL and the production
container; see [CI](ci.md).

## Usage and verification

Each run uses at most three Responses API requests. One third of the web-tool allowance
is split between primary and complementary discovery, and the remainder goes to
verification (default 3 + 2 + 10). With fewer than six allowed tool calls the second pass
is budget-skipped and the run is visibly partial. Empty first-pass results still trigger
the second pass. Output limits are 4,500 tokens per discovery and 9,000–18,000 for verification.
Run history stores provider-reported input/output tokens, observed web-tool calls,
billable search counts, stage-level usage and rate snapshots, candidate counts,
timestamps and sanitized errors. Billable search counts prefer provider
`tool_usage.web_search.num_requests`; otherwise only known, completed search actions
are counted. Open-page and find actions do not add a search fee. Token estimates
separate uncached input, cached reads, cache writes and output. Optional cached-read,
30-minute-write and 12-hour-write rates can be supplied per model stage. Without a
cached-read rate, full input pricing is explicitly labelled as conservative. Missing
write rates or incomplete usage produce an unavailable estimate rather than zero.
If a request fails without returning usage, the full-run estimate stays unavailable;
`knownEstimatedUsd` retains priced stages without implying the missing request was free.
Some interrupted requests can incur
cost without a returned usage report. These controls limit work; they are not a hard
dollar spending cap. Use the OpenAI project's Usage view for actual billing.

The administrator sees UTC daily search checks and each run's actual window, requested
scope, returned queries, pass status, deferred verification and remaining limitations.
Requested categories do not prove those categories were searched exhaustively. Missing
queries, repeated primary queries, reached limits and failed passes remain visible.
Legacy runs without coverage records are not counted as completed sweeps. Migration 11
adds separate JSON coverage storage without changing usage history or enabling AI.
No search status claims that every maritime incident on the open web has been captured.
See the [agreed discovery scope and verification plan](specs/discovery-coverage/spec.md).

Automated tests inject synthetic Responses payloads and block external HTTP. They cover
the independent switches, schedule and quota, overlapping calls, pausing at a paid
boundary, malformed/refused/incomplete responses, evidence provenance, stale reports,
duplicates, and the absence of publication or notifications. They do not establish live
model access, live source quality, or a measured per-run price.

## Local live pilot, 23 November 2026

The user requested real incident discovery using separate test credentials and a
private test Telegram bot. No production credentials or production database were
used. Diagnostic Luna discovery returned no candidates. A wider seven-day discovery
and verification sequence using Sol for both stages stored two pending reports and
delivered them to the test reviewer. Both still required further source verification;
neither was published. Model, prompt and time-window changes were combined, so this
does not establish a controlled model comparison or retrieval recall.

The completed paid discovery and verification stages together were estimated at
$0.371381 from returned usage and configured standard rates. All diagnostic stages
with returned usage totalled approximately $0.50. These are estimates, not reconciled
billing or measured daily spending. One verification request returned HTTP 503; its
missing usage cannot establish that it was free. Captured successful responses were
reprocessed locally without additional provider calls. Replaying the same verification
on a disposable archive copy left the candidate count at two, preventing two repeats.

A finite local pilot was configured for three runs per day, with a cutoff on
24 November. It requires the local process and computer to remain running. Feedback
prompt analysis was not enabled. Raw responses, databases and credentials are excluded
from Git and CI.

API contracts were checked against the official OpenAI documentation:
[web search](https://developers.openai.com/api/docs/guides/tools-web-search),
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
[GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna), and
[GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol).
