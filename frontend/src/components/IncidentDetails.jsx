import {
  safeReferenceUrl,
  hasCoordinates,
  locationAccuracyLabel,
} from "../data/incidents.js";

function valueText(value) {
  if (value === null || value === undefined || value === "")
    return "Not recorded";
  if (Array.isArray(value)) return value.map(valueText).join("; ");
  if (typeof value === "object") {
    if ("value" in value) return valueText(value.value);
    return JSON.stringify(value, null, 2);
  }
  return String(value);
}
function FieldValue({ value }) {
  const text = valueText(value),
    url = safeReferenceUrl(text);
  return url ? (
    <a href={url} target="_blank" rel="noopener noreferrer">
      {text}
    </a>
  ) : (
    <span>{text}</span>
  );
}
export function IncidentDetails({ incident, loading, error, onShowMap }) {
  if (loading && !incident) return <p role="status">Loading incident…</p>;
  if (!incident)
    return <p role="alert">{error || "Incident could not be loaded."}</p>;
  const references =
    incident.sources?.filter((source) => safeReferenceUrl(source.url)) ?? [];
  const sourceUrl = safeReferenceUrl(incident.sourceUrl);
  return (
    <div className="incident-details">
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      <h2>{incident.title}</h2>
      <p className="incident-summary">
        {incident.description || "No summary recorded."}
      </p>
      <dl className="detail-fields">
        <dt>Incident date</dt>
        <dd>
          {incident.dateText || incident.year || "Not recorded"}
          {!incident.dateText &&
          incident.yearEnd &&
          incident.yearEnd !== incident.year
            ? ` – ${incident.yearEnd}`
            : ""}
        </dd>
        <dt>Incident location</dt>
        <dd>
          {[incident.locationName, incident.incidentCountry]
            .filter(Boolean)
            .join(" · ") || "Not recorded"}
        </dd>
        <dt>Location accuracy</dt>
        <dd>
          {hasCoordinates(incident)
            ? locationAccuracyLabel(incident.locationPrecision) ||
              "Source coordinate; precision not specified"
            : "Unmapped — no reliable coordinates recorded"}
        </dd>
        <dt>Coordinates</dt>
        <dd>
          {hasCoordinates(incident)
            ? `${incident.latitude}, ${incident.longitude}`
            : "Not recorded"}
        </dd>
        {incident.locationSource && (
          <>
            <dt>Location source</dt>
            <dd>{incident.locationSource}</dd>
          </>
        )}
        <dt>Victim country</dt>
        <dd>{incident.victimCountry || "Not recorded"}</dd>
        <dt>Victim type</dt>
        <dd>{incident.victimType || "Not recorded"}</dd>
        <dt>Impact area</dt>
        <dd>{incident.impactArea || "Not recorded"}</dd>
        <dt>Threat actor country</dt>
        <dd>{incident.attackerCountry || "Not recorded"}</dd>
      </dl>
      {hasCoordinates(incident) && (
        <button className="primary-action" onClick={onShowMap}>
          Show on map
        </button>
      )}
      {(sourceUrl || references.length > 0) && (
        <section className="detail-section">
          <h3>References</h3>
          <ul>
            {references.length ? (
              references.map((source, i) => (
                <li key={`${source.url}-${i}`}>
                  <a
                    href={safeReferenceUrl(source.url)}
                    target="_blank"
                    rel="noopener noreferrer"
                    title={source.shortName || undefined}
                  >
                    {source.url}
                  </a>
                </li>
              ))
            ) : (
              <li>
                <a href={sourceUrl} target="_blank" rel="noopener noreferrer">
                  {sourceUrl}
                </a>
              </li>
            )}
          </ul>
        </section>
      )}
      {Object.keys(incident.rawFields ?? {}).length === 0 && (
        <section className="detail-section">
          <h3>Classification</h3>
          <dl className="detail-fields">
            {[
              ["Victim identity", "victimIdentity"],
              ["Affected system", "affectedSystem"],
              ["Method", "method"],
              ["Social engineering technique", "socialEngineeringTechnique"],
              ["Malware type", "malwareType"],
              ["Ransomware type", "ransomwareType"],
              ["Attack pattern", "attackPattern"],
              ["Motive", "motive"],
              ["Campaign", "campaign"],
              ["Impact", "impact"],
              ["Countermeasures", "countermeasures"],
              ["Threat actor", "threatActor"],
              ["Additional information", "additionalInfo"],
            ]
              .filter(([, key]) => incident[key])
              .map(([label, key]) => (
                <div className="detail-field" key={key}>
                  <dt>{label}</dt>
                  <dd>
                    <FieldValue value={incident[key]} />
                  </dd>
                </div>
              ))}
          </dl>
        </section>
      )}
      {Object.keys(incident.rawFields ?? {}).length > 0 && (
        <section className="detail-section">
          <h3>Original record</h3>
          <dl className="detail-fields original-fields">
            {Object.entries(incident.rawFields)
              .filter(([label]) => !label.startsWith("_"))
              .map(([label, value]) => (
                <div className="detail-field" key={label}>
                  <dt>{label.trim() || "Unnamed field"}</dt>
                  <dd>
                    <FieldValue value={value} />
                  </dd>
                </div>
              ))}
          </dl>
        </section>
      )}
      {incident.provenance?.length > 0 && (
        <p className="record-provenance">
          Imported source row{incident.provenance.length > 1 ? "s" : ""}:{" "}
          {incident.provenance.map((item) => item.sourceRow).join(", ")}
        </p>
      )}
    </div>
  );
}
