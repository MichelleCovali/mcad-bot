const environment = import.meta.env ?? {};
export const API_BASE_URL = environment.VITE_API_BASE_URL?.trim() || "/api";
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
  "locationName",
  "description",
  "dateText",
  "datePrecision",
  "locationPrecision",
  "locationSource",
  "sourceUrl",
  "externalId",
  "status",
  "createdAt",
  "updatedAt",
  "qa",
  "referenceNumber",
  "month",
  "victimIdentity",
  "victimTypeShort",
  "affectedSystem",
  "method",
  "socialEngineeringTechnique",
  "malwareType",
  "ransomwareType",
  "attackPattern",
  "motive",
  "motiveShort",
  "campaign",
  "impact",
  "impactShort",
  "countermeasures",
  "threatActor",
  "additionalInfo",
  "summary",
  "referenceShort",
  "googleMapLocation",
  "dateStart",
];
function invalidRecord(index, message) {
  throw new Error(`Incident ${index + 1}: ${message}`);
}
export function safeReferenceUrl(value) {
  if (typeof value !== "string") return null;
  try {
    const parsed = new URL(value);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : null;
  } catch {
    return null;
  }
}
export function locationAccuracyLabel(value) {
  if (
    ["source_provided", "source_coordinate", "source_coordinates"].includes(
      value,
    )
  )
    return "Source coordinates (approximate; not independently verified)";
  return (
    {
      exact: "Exact site (as recorded)",
      city: "City / port approximation",
      region: "Region approximation",
      country: "Country approximation",
      unknown: "Accuracy not recorded",
    }[value] || String(value ?? "").replaceAll("_", " ")
  );
}
export function hasCoordinates(record) {
  return (
    Number.isFinite(record.latitude) &&
    Number.isFinite(record.longitude) &&
    Math.abs(record.latitude) <= 90 &&
    Math.abs(record.longitude) <= 180
  );
}
export function validateIncidents(payload) {
  const records = Array.isArray(payload) ? payload : payload?.items;
  if (!Array.isArray(records))
    throw new Error(
      "The incidents API must return an array or an object with an items array.",
    );
  const identifiers = new Set();
  return records.map((record, index) => {
    if (!record || typeof record !== "object" || Array.isArray(record))
      invalidRecord(index, "expected an object.");
    if (
      !(typeof record.id === "string" && record.id.trim()) &&
      !(Number.isSafeInteger(record.id) && record.id >= 0)
    )
      invalidRecord(
        index,
        "id must be a non-empty string or a non-negative integer.",
      );
    const id = String(record.id).trim();
    if (identifiers.has(id)) invalidRecord(index, "id must be unique.");
    identifiers.add(id);
    if (typeof record.title !== "string" || !record.title.trim())
      invalidRecord(index, "title must be a non-empty string.");
    const latitude = record.latitude ?? null,
      longitude = record.longitude ?? null;
    if ((latitude === null) !== (longitude === null))
      invalidRecord(
        index,
        "latitude and longitude must both be present or both be null.",
      );
    if (latitude !== null) {
      for (const [field, value, minimum, maximum] of [
        ["latitude", latitude, -90, 90],
        ["longitude", longitude, -180, 180],
      ]) {
        if (!Number.isFinite(value) || value < minimum || value > maximum)
          invalidRecord(
            index,
            `${field} must be a number between ${minimum} and ${maximum}.`,
          );
      }
    }
    const year = record.year ?? null;
    const currentYear = new Date().getUTCFullYear();
    if (
      year !== null &&
      (!Number.isInteger(year) || year < 1900 || year > currentYear)
    )
      invalidRecord(
        index,
        `year must be an integer from 1900 to ${currentYear}, or null.`,
      );
    const incident = {
      id,
      title: record.title.trim(),
      latitude,
      longitude,
      year,
    };
    for (const field of textFields) {
      if (record[field] != null && typeof record[field] !== "string")
        invalidRecord(index, `${field} must be a string or null.`);
      incident[field] = record[field]?.trim() ?? "";
    }
    for (const field of ["rawFields", "sourceMetadata"]) {
      if (
        record[field] != null &&
        (typeof record[field] !== "object" || Array.isArray(record[field]))
      )
        invalidRecord(index, `${field} must be an object.`);
      if (record[field]) incident[field] = record[field];
    }
    for (const field of [
      "sources",
      "actors",
      "locations",
      "organisations",
      "provenance",
    ])
      if (Array.isArray(record[field])) incident[field] = record[field];
    if (Number.isInteger(record.yearEnd)) incident.yearEnd = record.yearEnd;
    return incident;
  });
}
async function requestJson(
  url,
  {
    fetchImpl = globalThis.fetch,
    signal,
    token,
    method = "GET",
    body,
    rawBody,
    contentType,
    filename,
  } = {},
) {
  if (signal?.aborted) {
    if (signal.throwIfAborted) signal.throwIfAborted();
    throw new DOMException("The request was aborted.", "AbortError");
  }
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (contentType) headers["Content-Type"] = contentType;
  if (filename) headers["X-Workbook-Filename"] = encodeURIComponent(filename);
  if (token) headers.Authorization = `Bearer ${token}`;
  let response;
  try {
    response = await fetchImpl(url, {
      method,
      headers,
      signal,
      ...(rawBody !== undefined
        ? { body: rawBody }
        : body === undefined
          ? {}
          : { body: JSON.stringify(body) }),
      cache: "no-store",
    });
  } catch (error) {
    if (signal?.aborted || error?.name === "AbortError") throw error;
    throw new Error(
      "Could not load incidents. Check the API connection and CORS settings.",
      { cause: error },
    );
  }
  if (!response.ok) {
    if (response.status === 401 || response.status === 403)
      throw new Error(
        "Administrator access was denied. Check your access token.",
      );
    let detail;
    try {
      const json = await response.json();
      detail = typeof json.detail === "string" ? json.detail : null;
    } catch {
      /* keep bounded generic HTTP error */
    }
    throw new Error(
      detail || `The incidents API returned HTTP ${response.status}.`,
    );
  }
  try {
    return await response.json();
  } catch (error) {
    if (signal?.aborted || error?.name === "AbortError") throw error;
    throw new Error("The incidents API returned invalid JSON.", {
      cause: error,
    });
  }
}
export function createIncidentClient({
  baseUrl = API_BASE_URL,
  fetchImpl = globalThis.fetch,
} = {}) {
  const base = String(baseUrl).trim().replace(/\/+$/, "");
  return async function listIncidents({ signal } = {}) {
    if (!base) {
      if (signal?.aborted) signal.throwIfAborted();
      return [];
    }
    return validateIncidents(
      await requestJson(`${base}/incidents`, { fetchImpl, signal }),
    );
  };
}
export const listIncidents = createIncidentClient();
export async function fetchIncidentSnapshot({ signal } = {}) {
  const payload = await requestJson(`${API_BASE_URL}/incidents`, { signal });
  const items = validateIncidents(payload);
  const mapped = items.filter(hasCoordinates).length;
  return {
    items,
    meta: {
      total: items.length,
      mapped,
      unmapped: items.length - mapped,
      ...(payload.meta ?? {}),
    },
  };
}
export async function fetchIncident(id, { signal } = {}) {
  const payload = await requestJson(
    `${API_BASE_URL}/incidents/${encodeURIComponent(id)}`,
    { signal },
  );
  return validateIncidents([payload.item ?? payload])[0];
}
export function createAdminClient(
  token,
  { baseUrl = API_BASE_URL, fetchImpl = globalThis.fetch } = {},
) {
  const cleanToken = String(token).trim();
  if (!cleanToken) throw new Error("Enter your administrator access token.");
  const request = (path, options = {}) =>
    requestJson(`${baseUrl}${path}`, {
      ...options,
      token: cleanToken,
      fetchImpl,
    });
  return {
    candidates: async ({ signal } = {}) => {
      const payload = await request("/admin/candidates", { signal });
      return Array.isArray(payload) ? payload : (payload.items ?? []);
    },
    create: (record) => request("/incidents", { method: "POST", body: record }),
    approve: (id, record) =>
      request(`/admin/candidates/${encodeURIComponent(id)}/approve`, {
        method: "POST",
        body: record,
      }),
    reject: (id, feedback) =>
      request(`/admin/candidates/${encodeURIComponent(id)}/reject`, {
        method: "POST",
        body: feedback,
      }),
    importWorkbook: async (file, { signal } = {}) => {
      if (
        !file ||
        typeof file.arrayBuffer !== "function" ||
        !String(file.name).toLowerCase().endsWith(".xlsx")
      )
        throw new Error("Choose an .xlsx workbook.");
      if (file.size <= 0 || file.size > 10 * 1024 * 1024)
        throw new Error("Choose a non-empty workbook of 10 MB or less.");
      return request("/admin/import-workbook", {
        method: "POST",
        rawBody: file,
        contentType:
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename: file.name,
        signal,
      });
    },
  };
}
function normalizeSearch(value) {
  return String(value ?? "")
    .normalize("NFKD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase();
}
export function filterIncidents(incidents, query, filterField = "all") {
  if (!Array.isArray(incidents))
    throw new TypeError("incidents must be an array.");
  if (!INCIDENT_FILTER_FIELDS.includes(filterField))
    throw new RangeError(`Unknown incident filter: ${filterField}`);
  const search = normalizeSearch(query).trim();
  if (!search) return incidents.slice();
  const fields =
    filterField === "all" ? ["title", ...textFields, "year"] : [filterField];
  return incidents.filter((incident) =>
    fields.some((field) => normalizeSearch(incident[field]).includes(search)),
  );
}
