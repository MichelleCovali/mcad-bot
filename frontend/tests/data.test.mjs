import assert from "node:assert/strict";
import test from "node:test";
import {
  createIncidentClient,
  createAdminClient,
  hasCoordinates,
  safeReferenceUrl,
  filterIncidents,
  validateIncidents,
} from "../src/data/incidents.js";
import { createIncidentIndex } from "../src/data/clusters.js";
import { searchPlaces } from "../src/data/places.js";
import { candidateDraft, reviewedIncidentPayload, submitReviewedIncident } from "../src/data/review.js";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

// Test-only inputs. These are never bundled as default map records.
const record = (changes = {}) => ({
  id: "test-1",
  title: "Validation fixture",
  latitude: 52,
  longitude: 4,
  victimCountry: "Netherlands",
  year: 2024,
  victimType: "Port",
  impactArea: "Operations",
  incidentCountry: "Belgium",
  attackerCountry: "Unknown",
  ...changes,
});

test("an unconfigured client returns no records and makes no request", async () => {
  const client = createIncidentClient({
    baseUrl: "",
    fetchImpl: () => assert.fail("Unexpected request"),
  });
  assert.deepEqual(await client(), []);
});

test("API client appends /incidents, supports wrapped results, and forwards cancellation", async () => {
  const controller = new AbortController();
  let request;
  const client = createIncidentClient({
    baseUrl: "https://example.invalid/api/",
    fetchImpl: async (...args) => {
      request = args;
      return {
        ok: true,
        json: async () => ({ items: [record({ id: 0, extra: "ignored" })] }),
      };
    },
  });
  const result = await client({ signal: controller.signal });
  assert.equal(request[0], "https://example.invalid/api/incidents");
  assert.equal(request[1].signal, controller.signal);
  assert.equal(request[1].headers.Accept, "application/json");
  assert.equal(result[0].id, "0");
  assert.equal("extra" in result[0], false);
});

test("API errors never silently turn into empty records", async (t) => {
  for (const [name, fetchImpl, expected] of [
    ["HTTP failure", async () => ({ ok: false, status: 503 }), /HTTP 503/],
    [
      "network failure",
      async () => {
        throw new TypeError("Failed to fetch");
      },
      /API connection and CORS/,
    ],
    [
      "invalid JSON",
      async () => ({
        ok: true,
        json: async () => {
          throw new SyntaxError();
        },
      }),
      /invalid JSON/,
    ],
    [
      "invalid structure",
      async () => ({ ok: true, json: async () => ({ records: [] }) }),
      /items array/,
    ],
    [
      "invalid record",
      async () => ({ ok: true, json: async () => [record({ latitude: 91 })] }),
      /latitude/,
    ],
  ]) {
    await t.test(name, async () => {
      await assert.rejects(
        createIncidentClient({ baseUrl: "/api", fetchImpl })(),
        expected,
      );
    });
  }
});

test("AbortError is preserved, including an already aborted signal", async () => {
  const aborted = new DOMException("Cancelled", "AbortError");
  const client = createIncidentClient({
    baseUrl: "/api",
    fetchImpl: async () => {
      throw aborted;
    },
  });
  await assert.rejects(client(), (error) => error === aborted);

  const controller = new AbortController();
  controller.abort();
  await assert.rejects(client({ signal: controller.signal }), {
    name: "AbortError",
  });
});

test("validation accepts coordinate boundaries and normalizes missing metadata", () => {
  assert.deepEqual(validateIncidents([]), []);
  const [result] = validateIncidents([
    {
      id: " edge ",
      title: " Boundary ",
      latitude: -90,
      longitude: 180,
    },
  ]);
  assert.equal(result.id, "edge");
  assert.equal(result.title, "Boundary");
  assert.equal(result.year, null);
  assert.equal(result.victimCountry, "");
  assert.equal(
    validateIncidents([record({ latitude: 90, longitude: -180 })]).length,
    1,
  );
});

test("validation rejects malformed coordinates, identifiers, years, and metadata", () => {
  const invalid = [
    [null, /object/],
    [record({ id: " " }), /id/],
    [record({ id: {} }), /id/],
    [record({ title: "" }), /title/],
    [record({ latitude: "52" }), /latitude/],
    [record({ latitude: NaN }), /latitude/],
    [record({ longitude: Infinity }), /longitude/],
    [record({ longitude: -180.1 }), /longitude/],
    [record({ year: "2024" }), /year/],
    [record({ year: 2024.5 }), /year/],
    [record({ year: 1899 }), /year/],
    [record({ year: new Date().getUTCFullYear() + 1 }), /year/],
    [record({ victimCountry: [] }), /victimCountry/],
  ];
  for (const [input, expected] of invalid) {
    assert.throws(() => validateIncidents([input]), expected);
  }
  assert.throws(
    () => validateIncidents([record({ id: 1 }), record({ id: "1" })]),
    /unique/,
  );
});

test("filters search the selected country, year, or type without mutating records", () => {
  const records = validateIncidents([
    record(),
    record({
      id: "test-2",
      victimCountry: "France",
      incidentCountry: "Netherlands",
      year: 2023,
      victimType: "Vessel",
    }),
  ]);
  const before = structuredClone(records);
  assert.deepEqual(
    filterIncidents(records, "nETHERlands", "victimCountry").map(
      ({ id }) => id,
    ),
    ["test-1"],
  );
  assert.deepEqual(
    filterIncidents(records, "netherlands", "incidentCountry").map(
      ({ id }) => id,
    ),
    ["test-2"],
  );
  assert.equal(filterIncidents(records, "2023", "year")[0].id, "test-2");
  assert.equal(
    filterIncidents(records, "vessel", "victimType")[0].id,
    "test-2",
  );
  assert.equal(filterIncidents(records, "operations", "impactArea").length, 2);
  assert.equal(filterIncidents(records, "validation").length, 2);
  assert.deepEqual(filterIncidents(records, "unmatched"), []);
  assert.deepEqual(filterIncidents(records, "  "), records);
  assert.deepEqual(records, before);
  assert.throws(
    () => filterIncidents(records, "x", "invalid"),
    /Unknown incident filter/,
  );
});

test("place search is local, case and accent insensitive, with useful aliases", () => {
  assert.deepEqual(searchPlaces(""), []);
  assert.equal(searchPlaces("rotter")[0].id, "rotterdam");
  assert.equal(searchPlaces("NETHERLANDS")[0].id, "netherlands");
  assert.equal(searchPlaces("Belgie")[0].id, "belgium");
  assert.equal(searchPlaces("Нидерланды")[0].id, "netherlands");
  assert.deepEqual(searchPlaces("a place not in this gazetteer"), []);
  assert.ok(searchPlaces("a").length <= 6);
  for (const place of searchPlaces("a")) {
    assert.ok(Number.isFinite(place.lat) && Number.isFinite(place.lng));
    assert.ok(place.zoom > 0);
  }
});

test("unmapped records remain searchable without inventing coordinates", () => {
  const records = validateIncidents([
    record(),
    record({
      id: "unknown",
      latitude: null,
      longitude: null,
      title: "Unmapped record",
    }),
  ]);
  assert.equal(records.length, 2);
  assert.equal(records.filter(hasCoordinates).length, 1);
  assert.equal(records[1].latitude, null);
  assert.equal(filterIncidents(records, "unmapped")[0].id, "unknown");
  assert.throws(
    () => validateIncidents([record({ latitude: null })]),
    /both be present/,
  );
});

test("detail validation preserves every original field and public provenance", () => {
  const rawFields = {
    "Victim identity": "Company",
    "Google Map Location": '=HYPERLINK("https://example.org", "Map")',
    "Additional info": "<script>not executable</script>",
  };
  const [detail] = validateIncidents([
    record({
      rawFields,
      organisations: [{ name: "Company" }],
      provenance: [{ sourceRow: 42 }],
      affectedSystem: "Navigation",
      method: "Malware",
    }),
  ]);
  assert.deepEqual(detail.rawFields, rawFields);
  assert.equal(detail.provenance[0].sourceRow, 42);
  assert.equal(detail.organisations[0].name, "Company");
  assert.equal(detail.affectedSystem, "Navigation");
  assert.equal(filterIncidents([detail], "navigation").length, 1);
});

test("reference links allow only public web protocols", () => {
  for (const input of [
    "javascript:alert(1)",
    "data:text/html,hello",
    "file:///tmp/test",
    "/relative",
    null,
  ])
    assert.equal(safeReferenceUrl(input), null);
  assert.equal(
    safeReferenceUrl("https://example.org/report"),
    "https://example.org/report",
  );
  assert.equal(safeReferenceUrl("http://example.org"), "http://example.org/");
});

test("admin writes carry a bearer token and preserve approval payload", async () => {
  const requests = [];
  const client = createAdminClient(" test-token ", {
    baseUrl: "/api",
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      return { ok: true, json: async () => ({ items: [] }) };
    },
  });
  await client.candidates();
  await client.approve("id/with space", {
    title: "Verified",
    latitude: null,
    longitude: null,
  });
  assert.equal(requests[0].options.headers.Authorization, "Bearer test-token");
  assert.equal(
    requests[1].url,
    "/api/admin/candidates/id%2Fwith%20space/approve",
  );
  assert.equal(requests[1].options.method, "POST");
  assert.deepEqual(JSON.parse(requests[1].options.body), {
    title: "Verified",
    latitude: null,
    longitude: null,
  });
  assert.throws(() => createAdminClient(" "), /access token/);
});

test("unauthorized admin access gives an actionable error", async () => {
  const client = createAdminClient("invalid", {
    fetchImpl: async () => ({ ok: false, status: 401 }),
  });
  await assert.rejects(client.candidates(), /access was denied/);
});

test("review history, rejection and AI settings use authenticated dedicated routes", async () => {
  const requests = [];
  const client = createAdminClient("test-token", {
    baseUrl: "/api",
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      return { ok: true, json: async () => ({ items: [], enabled: false }) };
    },
  });
  await client.candidates({ status: "rejected" });
  await client.candidates({ status: "approved" });
  await client.reject("report/a b", { reason: "insufficient_evidence", notes: "Only one original report." });
  await client.catalogs();
  await client.aiStatus();
  await client.saveAISettings({ enabled: false, runsPerDay: 4, lookbackHours: 48, maxCandidates: 5, maxToolCalls: 15 });
  assert.deepEqual(requests.map(({ url }) => url), [
    "/api/admin/candidates?status=rejected", "/api/admin/candidates?status=approved",
    "/api/admin/candidates/report%2Fa%20b/reject", "/api/admin/catalogs",
    "/api/admin/ai-monitor", "/api/admin/ai-monitor",
  ]);
  for (const request of requests) assert.equal(request.options.headers.Authorization, "Bearer test-token");
  assert.equal(requests[2].options.method, "POST");
  assert.equal(requests[5].options.method, "PATCH");
  assert.equal(JSON.parse(requests[5].options.body).enabled, false);
  assert.equal(requests.some(({ url }) => url.endsWith("/runs")), false);
  await client.runAI();
  assert.equal(requests[6].url, "/api/admin/ai-monitor/runs");
  assert.equal(requests[6].options.method, "POST");
  await assert.rejects(client.candidates({ status: "unknown" }), /valid review status/);
});

test("AI and review errors remain visible without being treated as empty success", async () => {
  for (const operation of ["aiStatus", "saveAISettings", "runAI", "reject"]) {
    const client = createAdminClient("test-token", {
      fetchImpl: async () => ({ ok: false, status: 409, json: async () => ({ detail: "AI search is disabled on this server." }) }),
    });
    await assert.rejects(client[operation]({ enabled: false }), /disabled on this server/);
  }
  const unavailable = createAdminClient("test-token", { fetchImpl: async () => { throw new TypeError("Offline"); } });
  await assert.rejects(unavailable.aiStatus(), /API connection/);
});

test("AI drafts preserve workbook fields while excluding unreviewed operational fields", () => {
  const draft = candidateDraft({
    title: "Candidate fallback", summary: "Source summary", sourceUrl: "https://example.invalid/a",
    proposedFields: { title: "Proposed title", victim_identity: "Fixture port", year: 2025, month: "",
      yearEnd: null, reference_urls: ["https://example.invalid/a", "https://example.invalid/b"],
      raw_fields: { "Latitude Longitude": "52, 4" }, status: "published", id: "injected", notification_suppressed: false },
  });
  assert.equal(draft.title, "Proposed title");
  assert.equal(draft.victimIdentity, "Fixture port");
  assert.equal(draft.summary, "Source summary");
  assert.equal(draft.coordinatesRaw, "52, 4");
  assert.equal(draft.referenceUrlsText, "https://example.invalid/a\nhttps://example.invalid/b");
  assert.equal(draft.status, undefined);
  assert.equal(draft.id, undefined);
  assert.equal(draft.notificationSuppressed, undefined);
});

test("review payload keeps source precision, multiple references and workbook classification", () => {
  const payload = reviewedIncidentPayload({
    title: "Reviewed fixture", summary: "Verified source text", year: "2025", month: "9", yearEnd: "2025",
    qa: "AB", referenceNumber: "20250901", victimTypeShort: "Port", motiveShort: "Unknown",
    impactShort: "Disruption", referenceShort: "Source (2025)", coordinatesRaw: "52.0, 4.0",
    latitude: "52", longitude: "4", locationPrecision: "source_provided",
    sourceUrl: "https://example.invalid/a", referenceUrlsText: "https://example.invalid/a\nhttps://example.invalid/b",
    id: "not-a-field", status: "published",
  });
  assert.equal(payload.summary, payload.description);
  assert.equal(payload.year, 2025);
  assert.equal(payload.month, "9");
  assert.equal(payload.yearEnd, 2025);
  assert.equal(payload.dateStart, undefined);
  assert.equal(payload.referenceNumber, "20250901");
  assert.equal(payload.victimTypeShort, "Port");
  assert.equal(payload.rawFields["Latitude Longitude"], "52.0, 4.0");
  assert.deepEqual(payload.referenceUrls, ["https://example.invalid/a", "https://example.invalid/b"]);
  assert.equal(payload.status, undefined);
  const unmapped = reviewedIncidentPayload({ title: "Only the year is known", year: "2024" });
  assert.equal(unmapped.month, undefined);
  assert.equal(unmapped.latitude, null);
  assert.equal(unmapped.locationPrecision, "unknown");
});

test("review rejects invented date precision, unsafe references and incomplete coordinates", () => {
  for (const values of [
    { month: "13" }, { month: "1.5" }, { year: "2024.5" }, { year: "2025", yearEnd: "2024" },
    { latitude: "52" }, { latitude: "91", longitude: "4", locationPrecision: "exact" },
    { latitude: "52", longitude: "4", locationPrecision: "unknown" },
    { sourceUrl: "javascript:alert(1)" }, { referenceUrlsText: "https://example.invalid/a\nfile:///private/data" },
  ]) assert.throws(() => reviewedIncidentPayload({ title: "Fixture", ...values }));
});

test("implicit form submission during rejection cannot publish or create an incident", async () => {
  const writes = [];
  const client = {
    approve: async (...args) => { writes.push(["approve", ...args]); return { id: "saved" }; },
    create: async (...args) => { writes.push(["create", ...args]); return { id: "created" }; },
  };
  const payload = reviewedIncidentPayload({ title: "Synthetic pending report" });
  const candidate = { id: "pending-report", status: "pending" };
  await assert.rejects(submitReviewedIncident(client, { candidate, payload, rejectionOpen: true }), /Complete or cancel rejection/);
  await assert.rejects(submitReviewedIncident(client, { candidate: null, payload, rejectionOpen: true }), /Complete or cancel rejection/);
  assert.deepEqual(writes, []);
  await assert.rejects(submitReviewedIncident(client, { candidate: { ...candidate, status: "rejected" }, payload }), /Only pending reports/);
  assert.deepEqual(writes, []);
  // Cancelling the rejection restores the explicit publication action.
  await submitReviewedIncident(client, { candidate, payload, rejectionOpen: false });
  assert.equal(writes.length, 1);
  assert.equal(writes[0][0], "approve");
  assert.equal(writes[0][1], "pending-report");
});

test("review preserves evidence metadata only for sources retained by the reviewer", () => {
  const draft = candidateDraft({ title: "Fixture", sourceUrl: "https://example.invalid/a", proposedFields: {
    referenceUrls: ["https://example.invalid/a", "https://second.invalid/b"],
    sources: [
      { url: "https://example.invalid/a", short_name: "Source A", raw_text: "Original evidence excerpt A.", secret: "do not forward" },
      { url: "https://second.invalid/b", shortName: "Source B", rawText: "Original evidence excerpt B." },
    ],
  } });
  // The reviewer removes A from both the primary URL and the reference list.
  const payload = reviewedIncidentPayload({ ...draft, sourceUrl: "https://SECOND.invalid/b#section", referenceUrlsText: "https://second.invalid/b\nhttps://manual.invalid/new" });
  assert.equal(payload.sources.length, 1);
  assert.ok(payload.sources.every((source) => source.shortName === "Source B" && source.rawText === "Original evidence excerpt B."));
  assert.ok(payload.sources.every((source) => !Object.hasOwn(source, "secret")));
  assert.ok(payload.sources.every((source) => !source.url.includes("example.invalid/a")));
  const manual = reviewedIncidentPayload({ title: "Manual fixture", referenceUrlsText: "https://manual.invalid/new" });
  assert.equal(manual.sources, undefined);
  assert.deepEqual(manual.referenceUrls, ["https://manual.invalid/new"]);
});

test("public detail keeps normalized fields visible beside AI provenance or partial original data", async () => {
  const { createServer } = await import("vite");
  const server = await createServer({
    configFile: false,
    server: { middlewareMode: true, hmr: false, watch: null },
    appType: "custom",
    esbuild: { jsx: "automatic" },
    logLevel: "silent",
  });
  try {
    const { IncidentDetails } = await server.ssrLoadModule("/src/components/IncidentDetails.jsx");
    const incident = record({ qa: "AB", referenceNumber: "fixture-ref", month: "9", yearEnd: 2024,
      victimTypeShort: "Port", method: "Navigation interference", motiveShort: "Unknown motive",
      impactShort: "Brief disruption", referenceShort: "Fixture citation",
      rawFields: { _aiReview: { evidence: {} } } });
    const internalOnly = renderToStaticMarkup(createElement(IncidentDetails, { incident }));
    assert.match(internalOnly, /Navigation interference/);
    assert.match(internalOnly, /fixture-ref/);
    assert.match(internalOnly, /Brief disruption/);
    assert.match(internalOnly, /Fixture citation/);
    assert.doesNotMatch(internalOnly, /Original record/);
    const partial = renderToStaticMarkup(createElement(IncidentDetails, { incident: { ...incident, rawFields: { _aiReview: {}, "Latitude Longitude": "52,4" } } }));
    assert.match(partial, /Navigation interference/);
    assert.match(partial, /Original record/);
    assert.match(partial, /52,4/);
    const original = renderToStaticMarkup(createElement(IncidentDetails, { incident: { ...incident, rawFields: { Method: "Navigation interference" } } }));
    assert.equal(original.match(/Navigation interference/g).length, 1);
  } finally { await server.close(); }
});

test("clusters keep co-located incidents distinct and exclude only unmapped records", () => {
  const records = validateIncidents([
    record(),
    record({ id: "same-place" }),
    record({ id: "unmapped", latitude: null, longitude: null }),
  ]);
  const index = createIncidentIndex(records);
  const clusters = index.getClusters([-180, -90, 180, 90], 5);
  assert.equal(clusters.length, 1);
  assert.equal(clusters[0].properties.point_count, 2);
  const leaves = index.getLeaves(clusters[0].properties.cluster_id, Infinity);
  assert.deepEqual(
    leaves.map((feature) => feature.properties.incident.id).sort(),
    ["same-place", "test-1"],
  );
});

test("workbook import uses the selected binary file and bearer authentication", async () => {
  const file = new File([new Uint8Array([80, 75, 3, 4])], "MCAD example.xlsx", {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
  let request;
  const client = createAdminClient("private-test-token", {
    baseUrl: "/api",
    fetchImpl: async (...args) => {
      request = args;
      return {
        ok: true,
        json: async () => ({ sourceRows: 1, notificationEventsCreated: 0 }),
      };
    },
  });
  const result = await client.importWorkbook(file);
  assert.equal(request[0], "/api/admin/import-workbook");
  assert.equal(request[1].method, "POST");
  assert.equal(request[1].body, file);
  assert.equal(request[1].headers.Authorization, "Bearer private-test-token");
  assert.equal(request[1].headers["Content-Type"], file.type);
  assert.equal(
    request[1].headers["X-Workbook-Filename"],
    "MCAD%20example.xlsx",
  );
  assert.equal(result.notificationEventsCreated, 0);
});

test("workbook import rejects wrong type, empty and oversized files before a request", async () => {
  const client = createAdminClient("test-token", {
    fetchImpl: () => assert.fail("Invalid file must not be uploaded"),
  });
  await assert.rejects(
    client.importWorkbook(new File(["text"], "data.csv")),
    /xlsx/,
  );
  await assert.rejects(
    client.importWorkbook(new File([], "empty.xlsx")),
    /non-empty/,
  );
  await assert.rejects(
    client.importWorkbook({
      name: "large.xlsx",
      size: 10 * 1024 * 1024 + 1,
      arrayBuffer: async () => new ArrayBuffer(0),
    }),
    /10 MB/,
  );
});
