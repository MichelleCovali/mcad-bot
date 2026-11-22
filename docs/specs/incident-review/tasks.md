# Incident review progress checklist

**Implemented** records code in the working tree. **Verified** names the checks that passed. **Remaining** identifies verification or work still open.

1. **Establish the baseline**
   1. **Implemented.** The branch had review endpoints, relationship guards, revision storage, and duplicate rejection. The working tree had an update comparison draft and API client changes.
   2. **Verified.** The isolated baseline passed 89 backend and 36 frontend tests with the local frontend work included. Seven opt-in backend checks were skipped. See the [baseline](../../project-overview.md).
   3. **Verified.** Inspected the three modified and three untracked frontend files and saved copies before editing. Reused the update components, selection helpers, and API client. The only repository caller of the apply-update route is the local frontend client.
   4. **Verified.** Compared the original branch range `7ef8ec8..7878f94` against the roadmap before implementation. The agreed release completes the review UI, stricter update eligibility and recovery, and revision access. The branch goals and their verification are listed below.

2. **Make updates safe on the backend** (after 1)
   1. **Implemented and verified, IR-03.** The protected apply-update route accepts pending unclassified, possible-update, and possible-duplicate reports. It verifies the published target and version-bound confirmation and allows seven text fields and supporting links. API tests cover missing confirmation, stale targets, disallowed fields, invalid sources, and compatible flagged and unflagged updates.
   2. **Implemented and verified, IR-04 and API portion of IR-07.** SQLite tests cover corrections backed by an existing source, source-only updates, no-op rejection, evidence preservation, rollback, and repeat submission. Disposable PostgreSQL 14 tests covered concurrent submissions for the same report and for two reports targeting the same incident version. Each produced one revision and no update alert.

3. **Expose revision history** (after 2)
   1. **Implemented and verified, IR-05.** API tests cover authentication, empty history, field changes, and source additions. The browser opened an applied update's revision from the approved report and displayed the recorded time and reason.

4. **Build the review journeys** (after 2.1)
   1. **Implemented and verified, IR-01, IR-02, IR-08.** In the fixture browser, opened a direct report link, navigated to separate settings, rejected a duplicate and reopened its feedback, and published a flagged report after the separate-event decision. The database recorded one publication outbox event and zero deliveries. AI and Telegram operations were disabled.
   2. **Implemented, IR-03, IR-04, IR-06, IR-07, IR-09.** Verified in the fixture browser that target and action switches dropped confirmation, reasons survived both switches, a stale save required reload and fresh confirmation, a source-only update saved, and an independently resolved report displayed its saved feedback and disabled writes.
   3. **Verified, IR-06 and IR-09.** On the fixture server, report drafts stayed separate and were restored on return. An eight-second target response finished after the faster selection without replacing it. A forced HTTP 503 on save preserved reason, evidence selection, and a manual retry. After a successful save, separate 503s on the queue and public refresh left the success message visible. Both retries recovered without another write, confirmed by the request log and revision rows.
   4. **Fixed and verified, IR-09 and IR-10.** Successful queue retry moves focus to the queue heading. Public retry moves focus to the saved incident link. The save result uses a persistent status region. Keyboard and browser accessibility checks confirmed both destinations and the exposed status.

   5. **Fixed and verified, IR-04.** The update source picker includes structured source entries even when their URLs are absent from the text list. Equivalent URLs appear once and retain recorded evidence.

   6. **Fixed and regression-tested, IR-04 and IR-07.** A stale Publish for an applied update returns a conflict and reloads the saved outcome. Source identity treats explicit HTTP and HTTPS default ports as equivalent while retaining stored URLs and metadata.

5. **Verify readiness** (after 2–4)
   1. **Verified in part, IR-10.** Used keyboard activation and checked focus on the action heading and the return to search when changing targets. The accessibility tree exposed labelled controls. At 390 px the review panel had no horizontal overflow and the queue was separately reachable.
   2. **Keyboard and accessibility tree verified, IR-10.** Activated all three decisions via keyboard in the fixture. Confirmed the update error received focus, save results were exposed as status updates, and focus returned to a meaningful control after refresh recovery.
   3. **Verified.** 95 backend tests passed, including five disposable PostgreSQL checks. Four opt-in private-workbook tests were skipped. 39 frontend tests, the production frontend build, and four Sites checks passed. The bundled frontend and API ran locally with fixture data and external workers disabled. The frontend suite, build, and Sites checks passed again after the focus fix.
   4. [ ] **Blocked, production container smoke test.** Docker could not connect to the configured OrbStack socket at `unix:///Users/mihaelacovali/.orbstack/run/docker.sock`. The default context at `unix:///var/run/docker.sock` also had no reachable daemon. Run the [local container commands](#local-production-container-smoke-test) once a daemon is reachable. The private workbook tests remain opt-in.

## Original branch goals

| Original goal | Implemented | Verified |
| --- | --- | --- |
| Define what counts as a maritime incident and preserve uncertainty | Yes, draft domain rules and AI review guidance | Repository rules inspected. Mocked tests cover conflicting evidence and sources that do not support confirmation. Client approval of the domain rules remains pending. |
| Detect likely duplicates and retain possible updates for review | Yes, conservative title and source matching and stored relationship metadata | Yes, mocked deduplication and database relationship tests. Live accuracy remains deferred. |
| Suppress alerts for related reports and guard ordinary publication | Yes | Yes, mocked notification and API regression tests. Browser confirmed a flagged report needed the separate-event decision before publication. |
| Let a reviewer resolve a separate event and preserve the reason | Yes | Yes, API test verifies reason, relationship history, and no alert on reclassification. Browser verified the decision before publication. |
| Apply reviewed changes to an existing incident and retain earlier evidence and one revision | Yes, including source-only updates and administrator history | Yes, SQLite and PostgreSQL tests plus fixture browser checks for stale recovery and source-only update. |
| Complete the agreed incident-review experience | Yes | Core browser journeys passed. The production container is tracked separately in step 5.4. |

Live AI evaluation, scheduled operation, historical research, advanced matching, and material-update alerts remain [separately scoped](../../roadmap.md). Telegram review interactions from other branches were outside this branch's agreed scope.

## Local production container smoke test

From the repository root, run this in a shell after `docker info` succeeds. The container is disposable, binds only to loopback, and uses an empty SQLite database.

```sh
(
  set -e
  docker build --tag mcad-release-smoke .
  container=$(docker run --detach --publish 127.0.0.1::8080 \
    --env DATABASE_URL=sqlite:////tmp/mcad-release.sqlite3 \
    --env ADMIN_API_TOKEN=local-smoke-only \
    --env ENABLE_BACKGROUND_WORKERS=false --env ENABLE_FEED_POLLING=false \
    --env ENABLE_TELEGRAM_POLLING=false --env ENABLE_AI_SEARCH=false \
    --env OPENAI_API_KEY= --env TELEGRAM_BOT_TOKEN= --env TELEGRAM_CHAT_ID= \
    mcad-release-smoke)
  trap 'docker rm --force "$container" >/dev/null' EXIT
  base="http://$(docker port "$container" 8080/tcp)"
  curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 1 \
    "$base/api/health" | python3 -c '
import json, sys
health = json.load(sys.stdin)
assert health["status"] == "ok" and health["database"] == "connected"
assert health["incidents"]["total"] == 0
assert not health["ai"]["serverEnabled"] and not health["telegram"]["configured"]
assert not health["feed"]["enabled"]
'
  curl --fail --silent --show-error "$base/" | grep -iq '<html'
  test "$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
    "$base/api/admin/ai-monitor")" = 401
)
```
