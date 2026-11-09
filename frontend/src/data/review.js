import { safeReferenceUrl } from "./incidents.js";

// Editable workbook columns plus the app's explicit date/location metadata.
export const REVIEW_FIELDS = [
  "qa", "referenceNumber", "month", "year", "yearEnd", "title", "summary",
  "impactArea", "locationName", "incidentCountry", "googleMapLocation",
  "victimCountry", "victimIdentity", "victimType", "victimTypeShort",
  "affectedSystem", "method", "socialEngineeringTechnique", "malwareType",
  "ransomwareType", "attackPattern", "motive", "motiveShort", "campaign",
  "impact", "impactShort", "attackerCountry", "countermeasures", "threatActor",
  "additionalInfo", "referenceShort", "sourceUrl", "dateText", "datePrecision",
  "latitude", "longitude", "locationPrecision", "locationSource",
];

const camelCase = (key) => key.replace(/_([a-z])/g, (_, letter) => letter.toUpperCase());
const referenceIdentity = (value) => {
  const safe = safeReferenceUrl(value);
  if (!safe) return null;
  const url = new URL(safe);
  if (url.username || url.password) return null;
  url.hash = "";
  return url.href;
};

export function candidateDraft(candidate) {
  if (!candidate) return { locationPrecision: "unknown" };
  const draft = { title: candidate.title, summary: candidate.summary,
    sourceUrl: candidate.sourceUrl, locationPrecision: "unknown" };
  const proposed = candidate.proposedFields;
  if (proposed && typeof proposed === "object" && !Array.isArray(proposed)) {
    for (const [sourceKey, value] of Object.entries(proposed)) {
      const key = camelCase(sourceKey);
      if (REVIEW_FIELDS.includes(key) && value !== null && value !== undefined)
        draft[key] = value;
      if (key === "description" && !proposed.summary) draft.summary = value ?? "";
    }
    const references = proposed.referenceUrls ?? proposed.reference_urls;
    if (Array.isArray(references)) draft.referenceUrlsText = references.filter((url) => typeof url === "string").join("\n");
    if (Array.isArray(proposed.sources)) {
      draft.referenceSources = proposed.sources.filter((source) => source && typeof source === "object" && referenceIdentity(source.url)).map((source) => ({
        url: source.url,
        shortName: typeof (source.shortName ?? source.short_name) === "string" ? source.shortName ?? source.short_name : "",
        rawText: typeof (source.rawText ?? source.raw_text) === "string" ? source.rawText ?? source.raw_text : "",
      }));
    }
    const raw = proposed.rawFields ?? proposed.raw_fields;
    if (raw && typeof raw === "object" && typeof raw["Latitude Longitude"] === "string")
      draft.coordinatesRaw = raw["Latitude Longitude"];
  }
  return draft;
}

export function reviewedIncidentPayload(values) {
  const payload = {};
  for (const key of REVIEW_FIELDS) {
    const value = values[key];
    if (value !== null && value !== undefined && String(value).trim() !== "") payload[key] = value;
  }
  if (typeof payload.title !== "string" || !payload.title.trim())
    throw new Error("Enter a verified incident title.");
  for (const key of ["year", "yearEnd", "latitude", "longitude"]) {
    if (payload[key] !== undefined) {
      payload[key] = Number(payload[key]);
      if (!Number.isFinite(payload[key])) throw new Error(`Enter a valid ${key}.`);
    }
  }
  for (const key of ["year", "yearEnd"]) {
    if (payload[key] !== undefined && (!Number.isInteger(payload[key]) || payload[key] < 1900 || payload[key] > new Date().getUTCFullYear()))
      throw new Error("Enter a whole year between 1900 and the current year.");
  }
  if (payload.year && payload.yearEnd && payload.yearEnd < payload.year)
    throw new Error("The ending year cannot precede the starting year.");
  if (payload.month !== undefined) {
    const month = Number(payload.month);
    if (!Number.isInteger(month) || month < 1 || month > 12) throw new Error("Enter a month from 1 to 12, or leave it blank.");
    payload.month = String(month);
  }
  const hasLat = payload.latitude !== undefined, hasLng = payload.longitude !== undefined;
  if (hasLat !== hasLng) throw new Error("Supply both latitude and longitude, or leave both blank.");
  if ((hasLat && Math.abs(payload.latitude) > 90) || (hasLng && Math.abs(payload.longitude) > 180))
    throw new Error("Enter valid latitude and longitude.");
  for (const key of ["sourceUrl", "googleMapLocation"]) {
    if (payload[key] && !referenceIdentity(payload[key])) throw new Error("Use HTTP or HTTPS reference and map URLs without embedded credentials.");
    if (payload[key]) payload[key] = key === "sourceUrl" ? referenceIdentity(payload[key]) : safeReferenceUrl(payload[key]);
  }
  const references = String(values.referenceUrlsText ?? "").split(/\r?\n/).map((url) => url.trim()).filter(Boolean);
  if (references.some((url) => !referenceIdentity(url))) throw new Error("Enter one HTTP or HTTPS reference URL per line, without embedded credentials.");
  payload.referenceUrls = [...new Set([...(payload.sourceUrl ? [payload.sourceUrl] : []), ...references.map(referenceIdentity)])];
  if (!payload.sourceUrl && payload.referenceUrls.length) payload.sourceUrl = payload.referenceUrls[0];
  if (Array.isArray(values.referenceSources)) {
    const metadata = new Map(values.referenceSources.filter((source) => source && referenceIdentity(source.url)).map((source) => [referenceIdentity(source.url), source]));
    const retained = payload.referenceUrls.flatMap((url) => {
      const source = metadata.get(referenceIdentity(url));
      if (!source) return [];
      return [{ url, shortName: typeof source.shortName === "string" ? source.shortName : "", rawText: typeof source.rawText === "string" ? source.rawText : "" }];
    });
    if (retained.length) payload.sources = retained;
  }
  payload.summary ??= "";
  payload.description = payload.summary;
  payload.latitude ??= null;
  payload.longitude ??= null;
  if (!hasLat) payload.locationPrecision = "unknown";
  else if (!payload.locationPrecision || payload.locationPrecision === "unknown")
    throw new Error("Specify the location accuracy of the supplied coordinates.");
  if (String(values.coordinatesRaw ?? "").trim())
    payload.rawFields = { "Latitude Longitude": String(values.coordinatesRaw) };
  return payload;
}

export const REJECTION_PUBLICATION_BLOCK = "Complete or cancel rejection before publishing.";

export async function requestedReviewCandidate(client, records, search) {
  const id = new URLSearchParams(search).get("candidate");
  if (!id) return null;
  return records.find((candidate) => candidate.id === id) ?? await client.candidate(id);
}

export async function submitReviewedIncident(client, { candidate, payload, rejectionOpen = false }) {
  // All publication writes pass this guard, including implicit Enter submissions.
  if (rejectionOpen) throw new Error(REJECTION_PUBLICATION_BLOCK);
  if (candidate && candidate.status !== "pending")
    throw new Error("Only pending reports can be published from the review queue.");
  return candidate ? client.approve(candidate.id, payload) : client.create(payload);
}
