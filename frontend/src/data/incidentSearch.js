export const countryRoles = [
  ["any", "Any recorded role"],
  ["incidentCountry", "Incident location"],
  ["victimCountry", "Victim country"],
  ["attackerCountry", "Threat actor country"],
];

export const facetLabels = {
  method: "Incident type",
  country: "Country",
  year: "Year",
  victimType: "Victim type",
  impactArea: "Impact area",
};

const searchFields = [
  "title", "description", "summary", "referenceNumber", "dateText", "year",
  "locationName", "incidentCountry", "victimCountry", "attackerCountry",
  "victimIdentity", "victimType", "victimTypeShort", "impactArea", "method",
  "affectedSystem", "socialEngineeringTechnique", "malwareType", "ransomwareType",
  "attackPattern", "motive", "campaign", "impact", "threatActor", "sourceUrl",
];

const unknownValues = new Set(["?", "-", "unknown", "uknown", "n/a", "n.a.", "na", "not recorded", "not known"]);
const fields = Object.keys(facetLabels);
const collator = new Intl.Collator("en", { numeric: true, sensitivity: "base" });

function normalize(value) {
  return String(value ?? "")
    .normalize("NFKD")
    .replace(/\p{Diacritic}/gu, "")
    .replace(/\p{Dash_Punctuation}/gu, " ")
    .toLowerCase()
    .replace(/\s+/gu, " ")
    .trim();
}

function facetValues(value) {
  return String(value ?? "")
    .split(/[,;\n]+/u)
    .map((part) => part.trim().replace(/^and\s+/iu, ""))
    .filter(knownLabel);
}

function knownLabel(label) {
  const value = normalize(label);
  return value && !unknownValues.has(value);
}

function incidentYears({ year, yearEnd }) {
  if (!year) return [];
  const last = Number.isInteger(yearEnd) && yearEnd >= year && yearEnd <= new Date().getUTCFullYear()
    ? yearEnd
    : year;
  return Array.from({ length: last - year + 1 }, (_, index) => String(year + index));
}

export function createIncidentSearchIndex(incidents) {
  return incidents.map((incident) => {
    const countries = Object.fromEntries(
      countryRoles.slice(1).map(([role]) => [
        role,
        incident.countryFacets?.[role]?.filter(knownLabel) ?? facetValues(incident[role]),
      ]),
    );
    countries.any = Object.values(countries).flat();
    const labels = {
      country: countries,
      method: facetValues(incident.method),
      year: incidentYears(incident),
      victimType: facetValues(incident.victimType),
      impactArea: facetValues(incident.impactArea),
    };
    const values = Object.fromEntries(
      fields.filter((field) => field !== "country").map((field) => [
        field,
        new Set(labels[field].map(normalize)),
      ]),
    );
    values.country = Object.fromEntries(
      Object.entries(countries).map(([role, names]) => [role, new Set(names.map(normalize))]),
    );
    return {
      incident,
      labels,
      values,
      terms: [...searchFields.map((field) => incident[field]), ...countries.any].map(normalize).filter(Boolean),
      title: normalize(incident.title),
    };
  });
}

export function incidentFacetOptions(index, countryRole = "any") {
  const facets = Object.fromEntries(fields.map((field) => [field, new Map()]));
  for (const { labels } of index) {
    for (const field of fields) {
      const entries = field === "country" ? labels.country[countryRole] ?? labels.country.any : labels[field];
      for (const label of entries) {
        const key = normalize(label);
        if (!facets[field].has(key)) facets[field].set(key, { value: key, label });
      }
    }
  }
  return Object.fromEntries(fields.map((field) => [
    field,
    [...facets[field].values()].sort((a, b) => field === "year" ? Number(b.value) - Number(a.value) : collator.compare(a.label, b.label)),
  ]));
}

export function searchIncidentIndex(index, query, filters, countryRole = "any") {
  const tokens = normalize(query).split(" ").filter(Boolean);
  const selected = Object.entries(filters).filter(([, value]) => value);
  return index.filter(({ terms, values }) =>
    tokens.every((token) => terms.some((term) => term.includes(token))) &&
    selected.every(([field, value]) => {
      const options = field === "country" ? values.country[countryRole] ?? values.country.any : values[field];
      return options?.has(value);
    })
  ).map(({ incident }) => incident);
}

export function matchingTitles(index, query, matchIds, limit = 5) {
  const match = normalize(query);
  if (match.length < 2) return [];
  const relevant = index.filter(({ title, incident }) => title.includes(match) && matchIds.has(incident.id));
  return [
    ...relevant.filter(({ title }) => title.startsWith(match)),
    ...relevant.filter(({ title }) => !title.startsWith(match)),
  ].slice(0, limit).map(({ incident }) => incident);
}

export function sortIncidentResults(records, direction) {
  if (direction === "title") return [...records].sort((a, b) => collator.compare(a.title, b.title));
  const order = direction === "oldest" ? 1 : -1;
  return [...records].sort((a, b) => {
    if (!a.year || !b.year) return Number(Boolean(b.year)) - Number(Boolean(a.year));
    return order * (a.year - b.year || (Number(a.month) || 0) - (Number(b.month) || 0));
  });
}
