import assert from "node:assert/strict";
import test, { after, before } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

let server;
let DailySearchCoverage;
let RunSearchCoverage;

before(async () => {
  server = await createServer({
    configFile: false,
    server: { middlewareMode: true, hmr: false, watch: null },
    appType: "custom",
    esbuild: { jsx: "automatic" },
    optimizeDeps: { noDiscovery: true, entries: [] },
  });
  ({ DailySearchCoverage, RunSearchCoverage } = await server.ssrLoadModule(
    "/src/components/AIMonitorSettings.jsx",
  ));
});

after(async () => { await server?.close(); });

test("daily search checks keep unfinished and unaudited work visible without claiming complete incident coverage", () => {
  const html = renderToStaticMarkup(createElement(DailySearchCoverage, {
    coverage: {
      dateUTC: "2026-11-23", sweepRunsCompleted: 0, runsIncomplete: 1,
      runsWithoutAudit: 2, pendingVerification: 3, confirmedReports: 0,
      queries: [], latestCheckedAt: null, completenessGuaranteed: false,
    },
  }));
  assert.match(html, /Daily search checks \(UTC\).*2026-11-23/);
  assert.match(html, /Incomplete attempts/);
  assert.match(html, /Drafts awaiting source checks/);
  assert.match(html, /2 attempts have no search-check record and are excluded/);
  assert.match(html, /cannot guarantee that every maritime cyber incident has been found/);
  assert.match(html, /at least two independent sources/);
  assert.match(html, /Copied reports count as one/);
  assert.match(html, /No individual search queries were recorded/);
});

test("a skipped omission pass stays visibly incomplete and retains verification gaps", () => {
  const html = renderToStaticMarkup(createElement(RunSearchCoverage, {
    coverage: {
      windowStart: "2026-11-23T00:00:00Z", windowEnd: "2026-11-23T16:00:00Z",
      passes: [
        { name: "primary", status: "completed", leadsFound: 4, sourceCount: 8,
          queries: ["port ransomware November 23 2026"] },
        { name: "gap_check", status: "skipped_budget", leadsFound: 0, sourceCount: 0, queries: [] },
      ],
      verification: { status: "pending", leadsSelected: 1, leadsDeferred: 3, recheckedReports: 0, updatedReports: 0 },
      limitations: ["Three leads await a future source check."],
    },
  }));
  assert.match(html, /1 of 2 discovery passes completed/);
  assert.match(html, /Follow-up search for omissions: Not performed: tool budget/);
  assert.match(html, /3 deferred/);
  assert.match(html, /Three leads await a future source check/);
  assert.match(html, /port ransomware November 23 2026/);
  assert.doesNotMatch(html, /2 of 2 discovery passes completed/);
});

test("completed searches expose their queries while preserving the shared allowance and coverage limitation", () => {
  const html = renderToStaticMarkup(createElement(RunSearchCoverage, {
    coverage: {
      passes: [
        { name: "primary", status: "completed", leadsFound: 0, sourceCount: 5, queries: ["maritime cyber incident"] },
        { name: "gap_check", status: "completed", leadsFound: 0, sourceCount: 6, queries: ["interferencia GPS buques"] },
      ],
      verification: { status: "not_needed", leadsSelected: 0, leadsDeferred: 0, recheckedReports: 0, updatedReports: 0 },
    },
  }));
  assert.match(html, /2 of 2 discovery passes completed/);
  assert.match(html, /No candidates required verification/);
  assert.match(html, /interferencia GPS buques/);
  assert.match(html, /share the run’s tool allowance/);
  assert.match(html, /do not establish complete coverage of the open web/);
});

test("legacy runs and absent coverage counts are unknown rather than successful zero-incident checks", () => {
  const legacy = renderToStaticMarkup(createElement(RunSearchCoverage));
  const emptyLegacy = renderToStaticMarkup(createElement(RunSearchCoverage, { coverage: {} }));
  const missing = renderToStaticMarkup(createElement(DailySearchCoverage));
  const partial = renderToStaticMarkup(createElement(DailySearchCoverage, { coverage: {} }));
  assert.match(legacy, /This run has no search-check record/);
  assert.match(emptyLegacy, /This run has no search-check record/);
  assert.match(missing, /unavailable for earlier runs/);
  assert.match(partial, /<strong>—<\/strong>/);
  assert.doesNotMatch(partial, /<strong>0<\/strong>/);
});
