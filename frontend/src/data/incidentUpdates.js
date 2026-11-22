import { referenceIdentity } from "./review.js";

export const UPDATE_TEXT_FIELDS = [
  ["title", "Title"],
  ["summary", "Summary"],
  ["affectedSystem", "Affected systems"],
  ["impact", "Impact"],
  ["method", "Method"],
  ["countermeasures", "Countermeasures"],
  ["additionalInfo", "Additional information"],
];

export function incidentUpdateOptions(incident, draft) {
  if (!incident || !draft) return [];

  return UPDATE_TEXT_FIELDS.flatMap(([key, label]) => {
    const proposed = draft[key];
    if (typeof proposed !== "string" || !proposed.trim()) {
      return [];
    }

    const current = String(incident[key] ?? "");
    if (current.trim() === proposed.trim()) return [];

    return [
      {
        key,
        label,
        current,
        proposed: proposed.trim(),
      },
    ];
  });
}

export function selectedIncidentChanges(incident, draft, selectedKeys) {
  const options = incidentUpdateOptions(incident, draft);
  const allowed = new Map(options.map((option) => [option.key, option]));
  const changes = {};

  for (const key of selectedKeys) {
    if (!allowed.has(key)) {
      throw new Error(
        "A selected field is no longer available. Review it again.",
      );
    }
    changes[key] = allowed.get(key).proposed;
  }

  return changes;
}

export function incidentUpdateSources(draft, incident) {
  const reportSources = draft?.referenceSources ?? [];
  const existingSources = incident?.sources ?? [];
  const references = [
    draft?.sourceUrl,
    ...String(draft?.referenceUrlsText ?? "").split(/\r?\n/),
    ...reportSources.map((source) => source.url),
    incident?.sourceUrl,
    ...existingSources.map((source) => source.url),
  ];
  const metadata = new Map(
    [...reportSources, ...existingSources].map((source) => [
      referenceIdentity(source.url), source,
    ]),
  );
  const identities = new Set(references.map(referenceIdentity).filter(Boolean));

  return [...identities].map((url) => {
    const details = metadata.get(url) ?? {};
    return {
      url,
      shortName: typeof details.shortName === "string" ? details.shortName : "",
      rawText: typeof details.rawText === "string" ? details.rawText : "",
    };
  });
}
