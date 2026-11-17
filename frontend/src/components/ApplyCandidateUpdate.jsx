import { useEffect, useState } from "react";

// Intentional allowlist: updates never blindly overwrite incident IDs,
// published status, dates, locations, or coordinates from AI proposals.
const UPDATE_FIELDS = [
  ["description", "Description"],
  ["summary", "Summary"],
  ["impact", "Impact"],
  ["impactShort", "Impact (short)"],
  ["method", "Method / incident category"],
  ["affectedSystem", "Affected system"],
  ["countermeasures", "Countermeasures"],
  ["additionalInfo", "Additional information"],
  ["threatActor", "Threat actor"],
  ["victimType", "Victim type"],
  ["impactArea", "Impact area"],
];

function snakeCase(key) {
  return key.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`);
}

export function reviewedUpdatePayload({ selectedFields, fieldValues, reason, sourceText }) {
  if (typeof reason !== "string" || !reason.trim() || reason.length > 2000)
    throw new Error("Enter a review reason of up to 2000 characters.");
  const changes = {};
  for (const [key] of UPDATE_FIELDS) {
    if (!selectedFields.includes(key)) continue;
    const newValue = fieldValues[key];
    if (typeof newValue !== "string" || !newValue.trim())
      throw new Error(`Enter a verified value for ${key} or deselect it.`);
    changes[key] = newValue.trim();
  }
  const sourceUrls = sourceText.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  for (const sourceUrl of sourceUrls) {
    let parsed;
    try {
      parsed = new URL(sourceUrl);
    } catch {
      throw new Error("Enter valid HTTP/HTTPS source URLs, one per line.");
    }
    if (!["http:", "https:"].includes(parsed.protocol) ||
        parsed.username || parsed.password)
      throw new Error("Enter HTTP/HTTPS source URLs without embedded credentials.");
  }
  if (!Object.keys(changes).length && !sourceUrls.length)
    throw new Error("Select an updated field or add a verified source URL.");
  return { reason: reason.trim(), changes, sourceUrls };
}

export function ApplyCandidateUpdate({ candidate, client, busy, onApply }) {
  const [original, setOriginal] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [selectedFields, setSelectedFields] = useState([]);
  const [fieldValues, setFieldValues] = useState({});
  const [reason, setReason] = useState("");
  const [sourceText, setSourceText] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState("");
  const relatedIncidentId = candidate.relatedIncidentId;

  useEffect(() => {
    let active = true;
    setOriginal(null);
    setLoadError("");
    setSelectedFields([]);
    setFieldValues({});
    setReason("");
    setSourceText("");
    setConfirmed(false);
    setError("");
    client.incident(relatedIncidentId)
      .then((record) => { if (active) setOriginal(record); })
      .catch((err) => { if (active) setLoadError(err.message); });
    return () => { active = false; };
  }, [client, candidate.id, relatedIncidentId]);

  function apply() {
    setError("");
    try {
      if (!confirmed) throw new Error("Review the linked incident and confirm the evidence first.");
      onApply(reviewedUpdatePayload({ selectedFields, fieldValues, reason, sourceText }));
    } catch (failure) {
      setError(failure.message);
    }
  }

  return (
    <section className="candidate-evidence" aria-label="Apply verified update">
      <h3>Apply update to existing incident</h3>
      <p>
        Use this only when the report adds verified information to the linked
        incident. Select the fields you want to change. Unselected fields,
        existing sources and the original incident ID will be preserved.
      </p>
      <p className="admin-help">
        The candidate source URL will also be linked to the incident when the
        update is applied. No new incident or publication notification is created.
      </p>
      {loadError && <p role="alert">Could not load the existing incident: {loadError}</p>}
      {!original && !loadError && <p>Loading linked incident…</p>}
      {original && (
        <>
          <p><strong>Updating:</strong> {original.title} ({original.year ?? "year unknown"})</p>
          {UPDATE_FIELDS.map(([key, label]) => {
            const proposed = candidate.proposedFields?.[key] ??
              candidate.proposedFields?.[snakeCase(key)];
            return (
              <div key={key} className="review-feedback">
                <label>
                  <input
                    type="checkbox"
                    checked={selectedFields.includes(key)}
                    disabled={busy}
                    onChange={(event) => setSelectedFields((previous) =>
                      event.target.checked ? [...previous, key] : previous.filter((name) => name !== key))}
                  />
                  {` Update ${label}`}
                </label>
                {selectedFields.includes(key) && (
                  <>
                    <p className="admin-help">
                      <strong>Existing:</strong> {original[key] || "Not recorded"}
                    </p>
                    {typeof proposed === "string" && proposed.trim() && (
                      <p className="admin-help"><strong>AI proposal (unverified):</strong> {proposed}</p>
                    )}
                    <textarea
                      aria-label={`Verified new ${label}`}
                      rows={2}
                      value={fieldValues[key] ?? ""}
                      disabled={busy}
                      onChange={(event) => setFieldValues((previous) => ({
                        ...previous, [key]: event.target.value,
                      }))}
                      placeholder="Enter the verified replacement value"
                    />
                  </>
                )}
              </div>
            );
          })}
          <label>
            Additional verified source URLs (optional, one per line)
            <textarea
              rows={2}
              value={sourceText}
              disabled={busy}
              onChange={(event) => setSourceText(event.target.value)}
              placeholder="https://example.com/report"
            />
          </label>
          <label>
            Reason for applying the update *
            <textarea
              rows={3}
              maxLength={2000}
              value={reason}
              disabled={busy}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Explain which claims and sources you verified"
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={confirmed}
              disabled={busy}
              onChange={(event) => setConfirmed(event.target.checked)}
            />
            {" I compared the existing incident with the original sources and verified the changes."}
          </label>
          {error && <p role="alert">{error}</p>}
          <button
            type="button"
            className="secondary-action"
            disabled={busy || !confirmed || !reason.trim()}
            onClick={apply}
          >
            Apply verified update to existing incident
          </button>
        </>
      )}
    </section>
  );
}
