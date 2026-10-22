const environment = import.meta.env ?? {};

export const INCIDENT_FILTER_FIELDS = Object.freeze([
  "all",
  "victimCountry",
  "year",
  "victimType",
  "impactArea",
  "incidentCountry",
  "attackerCountry",
]);

const textFields = [
  "victimCountry",
  "victimType",
  "impactArea",
  "incidentCountry",
  "attackerCountry",
];

function invalidRecord(index, message) {
  throw new Error(`Incident ${index + 1}: ${message}`);
}

/** Validate untrusted API records and return only the supported public fields. */
export function validateIncidents(payload) {
  const records = Array.isArray(payload) ? payload : payload?.items;

  if (!Array.isArray(records)) {
    throw new Error(
      "The incidents API must return an array or an object with an items array.",
    );
  }

  const identifiers = new Set();
  const currentYear = new Date().getUTCFullYear();

  return records.map((record, index) => {
    if (!record || typeof record !== "object" || Array.isArray(record)) {
      invalidRecord(index, "expected an object.");
    }

    const validStringId =
      typeof record.id === "string" && record.id.trim().length > 0;
    const validNumericId = Number.isSafeInteger(record.id) && record.id >= 0;
    if (!validStringId && !validNumericId) {
      invalidRecord(
        index,
        "id must be a non-empty string or a non-negative integer.",
      );
    }

    const id = String(record.id).trim();
    if (identifiers.has(id)) {
      invalidRecord(index, "id must be unique.");
    }
    identifiers.add(id);

    if (typeof record.title !== "string" || !record.title.trim()) {
      invalidRecord(index, "title must be a non-empty string.");
    }

    for (const [field, minimum, maximum] of [
      ["latitude", -90, 90],
      ["longitude", -180, 180],
    ]) {
      if (
        !Number.isFinite(record[field]) ||
        record[field] < minimum ||
        record[field] > maximum
      ) {
        invalidRecord(
          index,
          `${field} must be a number between ${minimum} and ${maximum}.`,
        );
      }
    }

    const year = record.year ?? null;
    if (
      year !== null &&
      (!Number.isInteger(year) || year < 1900 || year > currentYear)
    ) {
      invalidRecord(
        index,
        `year must be an integer from 1900 to ${currentYear}, or null.`,
      );
    }

    const incident = {
      id,
      title: record.title.trim(),
      latitude: record.latitude,
      longitude: record.longitude,
      year,
    };

    for (const field of textFields) {
      if (record[field] != null && typeof record[field] !== "string") {
        invalidRecord(index, `${field} must be a string or null.`);
      }
      incident[field] = record[field]?.trim() ?? "";
    }

    return incident;
  });
}

/** Create an isolated client; injectable fetch keeps API behavior easy to test. */
export function createIncidentClient({
  baseUrl = environment.VITE_API_BASE_URL ?? "",
  fetchImpl = globalThis.fetch,
} = {}) {
  const base = String(baseUrl).trim().replace(/\/+$/, "");

  return async function listIncidents({ signal } = {}) {
    if (signal?.aborted) {
      if (signal.throwIfAborted) signal.throwIfAborted();
      throw new DOMException("The request was aborted.", "AbortError");
    }

    // An unconfigured development frontend intentionally has no incident markers.
    if (!base) return [];

    let response;
    try {
      response = await fetchImpl(`${base}/incidents`, {
        method: "GET",
        headers: { Accept: "application/json" },
        signal,
      });
    } catch (error) {
      if (signal?.aborted || error?.name === "AbortError") throw error;
      throw new Error(
        "Could not load incidents. Check the API connection and CORS settings.",
        {
          cause: error,
        },
      );
    }

    if (!response.ok) {
      throw new Error(`The incidents API returned HTTP ${response.status}.`);
    }

    let payload;
    try {
      payload = await response.json();
    } catch (error) {
      if (signal?.aborted || error?.name === "AbortError") throw error;
      throw new Error("The incidents API returned invalid JSON.", {
        cause: error,
      });
    }

    return validateIncidents(payload);
  };
}

export const listIncidents = createIncidentClient();

function normalizeSearch(value) {
  return String(value ?? "")
    .normalize("NFKD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase();
}

/** Search loaded records without changing the input array or its records. */
export function filterIncidents(incidents, query, filterField = "all") {
  if (!Array.isArray(incidents))
    throw new TypeError("incidents must be an array.");
  if (!INCIDENT_FILTER_FIELDS.includes(filterField)) {
    throw new RangeError(`Unknown incident filter: ${filterField}`);
  }

  const search = normalizeSearch(query).trim();
  if (!search) return incidents.slice();

  const fields =
    filterField === "all" ? ["title", ...textFields, "year"] : [filterField];
  return incidents.filter((incident) =>
    fields.some((field) => normalizeSearch(incident[field]).includes(search)),
  );
}
