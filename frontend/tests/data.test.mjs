import assert from "node:assert/strict";
import test from "node:test";
import { createIncidentClient, filterIncidents, validateIncidents } from "../src/data/incidents.js";
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
  const client = createIncidentClient({ baseUrl: "", fetchImpl: () => assert.fail("Unexpected request") });
  assert.deepEqual(await client(), []);
});

test("API client appends /incidents, supports wrapped results, and forwards cancellation", async () => {
  const controller = new AbortController();
  let request;
  const client = createIncidentClient({
    baseUrl: "https://example.invalid/api/",
    fetchImpl: async (...args) => {
      request = args;
      return { ok: true, json: async () => ({ items: [record({ id: 0, extra: "ignored" })] }) };
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
    ["network failure", async () => { throw new TypeError("Failed to fetch"); }, /API connection and CORS/],
    ["invalid JSON", async () => ({ ok: true, json: async () => { throw new SyntaxError(); } }), /invalid JSON/],
    ["invalid structure", async () => ({ ok: true, json: async () => ({ records: [] }) }), /items array/],
    ["invalid record", async () => ({ ok: true, json: async () => [record({ latitude: 91 })] }), /latitude/],
  ]) {
    await t.test(name, async () => {
      await assert.rejects(createIncidentClient({ baseUrl: "/api", fetchImpl })(), expected);
    });
  }
});

test("AbortError is preserved, including an already aborted signal", async () => {
  const aborted = new DOMException("Cancelled", "AbortError");
  const client = createIncidentClient({ baseUrl: "/api", fetchImpl: async () => { throw aborted; } });
  await assert.rejects(client(), (error) => error === aborted);

  const controller = new AbortController();
  controller.abort();
  await assert.rejects(client({ signal: controller.signal }), { name: "AbortError" });
});

test("validation accepts coordinate boundaries and normalizes missing metadata", () => {
  assert.deepEqual(validateIncidents([]), []);
  const [result] = validateIncidents([{
    id: " edge ", title: " Boundary ", latitude: -90, longitude: 180,
  }]);
  assert.equal(result.id, "edge");
  assert.equal(result.title, "Boundary");
  assert.equal(result.year, null);
  assert.equal(result.victimCountry, "");
  assert.equal(validateIncidents([record({ latitude: 90, longitude: -180 })]).length, 1);
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
  assert.throws(() => validateIncidents([record({ id: 1 }), record({ id: "1" })]), /unique/);
});

test("filters search the selected country, year, or type without mutating records", () => {
  const records = validateIncidents([
    record(),
    record({ id: "test-2", victimCountry: "France", incidentCountry: "Netherlands", year: 2023, victimType: "Vessel" }),
  ]);
  const before = structuredClone(records);
  assert.deepEqual(filterIncidents(records, "nETHERlands", "victimCountry").map(({ id }) => id), ["test-1"]);
  assert.deepEqual(filterIncidents(records, "netherlands", "incidentCountry").map(({ id }) => id), ["test-2"]);
  assert.equal(filterIncidents(records, "2023", "year")[0].id, "test-2");
  assert.equal(filterIncidents(records, "vessel", "victimType")[0].id, "test-2");
  assert.equal(filterIncidents(records, "operations", "impactArea").length, 2);
  assert.equal(filterIncidents(records, "validation").length, 2);
  assert.deepEqual(filterIncidents(records, "unmatched"), []);
  assert.deepEqual(filterIncidents(records, "  "), records);
  assert.deepEqual(records, before);
  assert.throws(() => filterIncidents(records, "x", "invalid"), /Unknown incident filter/);
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
