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
