# AI incident monitoring

The monitor is implemented but **paused by default**. Adding `OPENAI_API_KEY`, deploying,
opening the administrator panel, importing Excel, and saving a schedule do not start a search.
No live OpenAI request was made while implementing or testing this change.

## Configuration and activation

Keep `ENABLE_AI_SEARCH=false` in Railway while preparing the application. Keep the OpenAI
key in the backend service variables; it is never returned by the API or bundled into the
frontend. The administrator panel reports only whether a key is configured.

There are two independent switches:

1. The server environment variable `ENABLE_AI_SEARCH` defaults to `false`.
2. The saved administrator setting `enabled` also defaults to `false`.

Both switches and a configured key are required for any paid request, including a manual
run. The server blocks enabling the saved switch while its environment switch is off.
Later activation must be intentional: enable the server switch, then enable monitoring
in the administrator settings. A background schedule additionally requires
`ENABLE_BACKGROUND_WORKERS=true`. A manual run does not require background workers.
Pausing in the panel prevents new runs and is checked again immediately before each paid
stage and before candidate storage. It cannot cancel a provider request already in flight.

Settings allow **3, 4, or 6 runs per day** (intervals of 8, 6, or 4 hours), a reporting lookback
of 1–168 hours, 1–10 candidate proposals, and 2–30 web tool calls per run. Defaults are
3 runs/day, a 48-hour lookback, 5 candidates, and 15 tool calls. The schedule begins when
enabled and due; it is interval based rather than tied to local wall-clock hours.
Daily quotas use UTC and include manual, failed, and interrupted attempts because those
attempts may already have incurred usage. A database lease prevents overlapping workers
or manual runs. There are no automatic provider retries.

Administrator endpoints (all require the existing bearer token):

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
known classification mistakes. Retrieved pages and stored feedback are treated as
untrusted content, not instructions.

Structured results are checked locally before storage. Each evidence URL must appear
in the verifier's actual tool results or citation trail; excerpts and supported or
contradicted claims are retained. An assessment of `supported` requires two opened source
hosts with different reported origin groups and a usable first-reporting date. A copied
story from the same origin, missing freshness, or search snippets alone remains
`needs_review`. Conflicting claims are labelled `contradicted`. Known old reports are
excluded. Source independence and quoted claims still require human judgement: an
opened URL proves the research trail, not that every model claim is correct.

**Every result is a pending candidate. The monitor cannot publish an incident or create a
Telegram notification.** An administrator checks the evidence, edits the proposed MCAD
fields, and explicitly approves or rejects it. Rejection feedback is stored for review
and future prompt context; this is not model fine-tuning.

Candidate proposals follow the Excel incident structure. Unknown facts remain empty or
`null`; dates keep their source precision, coordinates are not guessed, and the AI cannot
assign curator initials or internal reference numbers. Original source excerpts,
contradictions, uncertainties, reporting dates, the research time and run ID are retained.
Publication records the reviewed proposal and evidence snapshot. Existing Excel import
behaviour and the current Telegram message format are unchanged.

Repeated title/year pairs and already-seen source sets are held back. A distinct event
may share a background reference with a known incident if it has a new source URL.
This is conservative duplicate prevention, not a complete event-identity resolver.

## Usage and verification

Each run uses at most two Responses API requests. One third of the web-tool allowance
goes to discovery and the remainder to verification (default 5 + 10). Empty discovery
results finish after the first request. Output limits are 4,500 and 9,000 tokens.
Run history stores provider-reported input/output tokens, observed web-tool calls,
candidate counts, timestamps and sanitized errors. Some interrupted requests can incur
cost without a returned usage report. These controls limit work; they are not a hard
dollar spending cap. Use the OpenAI project's Usage view for actual billing.

Automated tests inject synthetic Responses payloads and block external HTTP. They cover
the independent switches, schedule and quota, overlapping calls, pausing at a paid
boundary, malformed/refused/incomplete responses, evidence provenance, stale reports,
duplicates, and the absence of publication or notifications. They do not establish live
model access, live source quality, or a measured per-run price.

API contracts were checked against the official OpenAI documentation:
[web search](https://developers.openai.com/api/docs/guides/tools-web-search),
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
[GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna), and
[GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol).
