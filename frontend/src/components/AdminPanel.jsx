import { useEffect, useRef, useState } from "react";
import { fetchIncidentSnapshot, filterIncidents, safeReferenceUrl } from "../data/incidents.js";
import { candidateDraft, reviewedIncidentPayload, REJECTION_PUBLICATION_BLOCK, submitReviewedIncident, requestedReviewCandidate } from "../data/review.js";

const fields = [
  ["title", "Title", true],
  ["summary", "Summary", false, "textarea"],
  ["qa", "QA"],
  ["referenceNumber", "Reference number"],
  ["month", "Month (blank if unknown)", false, "number"],
  ["year", "Year", false, "number"],
  ["yearEnd", "Ending year (blank if same year)", false, "number"],
  ["dateText", "Date as reported"],
  ["incidentCountry", "Incident country"],
  ["locationName", "Incident location"],
  ["latitude", "Latitude", false, "number"],
  ["longitude", "Longitude", false, "number"],
  ["locationPrecision", "Location accuracy"],
  ["locationSource", "Coordinate source"],
  ["victimCountry", "Victim country"],
  ["victimType", "Victim type"],
  ["impactArea", "Impact area"],
  ["attackerCountry", "Threat actor country"],
  ["sourceUrl", "Reference URL", false, "url"],
  ["referenceUrlsText", "All reference URLs (one per line)", false, "textarea"],
  ["referenceShort", "Reference short", false, "textarea"],
  ["coordinatesRaw", "Original coordinate text (as reported)"],
  ["googleMapLocation", "Google Map Location", false, "url"],
];
const classification = [
  ["victimIdentity", "Victim identity"],
  ["victimTypeShort", "Victim type short"],
  ["affectedSystem", "Affected system"],
  ["method", "Method"],
  ["socialEngineeringTechnique", "Social engineering technique"],
  ["malwareType", "Malware type"],
  ["ransomwareType", "Ransomware type"],
  ["attackPattern", "Attack pattern"],
  ["motive", "Motive"],
  ["motiveShort", "Motive short"],
  ["campaign", "Campaign"],
  ["impact", "Impact"],
  ["impactShort", "Impact short"],
  ["countermeasures", "Countermeasures"],
  ["threatActor", "Threat actor"],
  ["additionalInfo", "Additional information"],
];
const rejectionLabels = {
  not_maritime: "Not maritime-related", not_cyber: "Not a cybersecurity incident",
  duplicate: "Duplicate incident", insufficient_evidence: "Insufficient evidence",
  incorrect_information: "Incorrect information", other: "Other",
};
const catalogFields = {
  incidentCountry: "countries", victimCountry: "countries", attackerCountry: "countries",
  victimType: "victim_types", impactArea: "impact_areas", method: "methods",
};

function EvidenceDetails({ candidate }) {
  const evidence = candidate.evidence;
  if (!evidence || typeof evidence !== "object" || !Object.keys(evidence).length) return null;
  const statuses = { supported: "Sources found — human review required", needs_review: "Further evidence needed", contradicted: "Conflicting reports" };
  return <section className="candidate-evidence">
    <h3>Source checks</h3>
    <p><strong>{statuses[candidate.verificationStatus] || "Not independently verified"}</strong></p>
    {Number.isInteger(candidate.independentSourceCount) && <p>{candidate.independentSourceCount} independently supported {candidate.independentSourceCount === 1 ? "origin" : "origins"} identified. Publishing requires at least two.</p>}
    {typeof evidence.assessment === "string" && <p>{evidence.assessment}</p>}
    <p className="admin-help">Source independence and supporting statements below are model assessments. Open the original reports and check them before publishing.</p>
    <dl className="detail-fields">
      {[["Checked", evidence.checkedAt], ["First reported", evidence.firstReportedAt], ["Event date", evidence.eventDate]].map(([label, value]) => value && <div className="detail-field" key={label}><dt>{label}</dt><dd>{String(value)}</dd></div>)}
    </dl>
    <ul>
      {Array.isArray(evidence.sources) && evidence.sources.map((source, index) => <li key={`${source.url}-${index}`}>
        {safeReferenceUrl(source.url) ? <a href={safeReferenceUrl(source.url)} target="_blank" rel="noopener noreferrer">{source.title || source.url}</a> : <strong>Source link unavailable</strong>}
        <p>{source.publisher || "Publisher unknown"} · Independence group: {source.independenceGroup || "unknown"}{source.isPrimary === true ? " · Reported primary source" : ""}</p>
        {source.snippet && <blockquote>{String(source.snippet)}</blockquote>}
        {source.supports && <p>Supports: {Array.isArray(source.supports) ? source.supports.join("; ") : String(source.supports)}</p>}
        {source.contradicts && <p>Contradicts: {Array.isArray(source.contradicts) ? source.contradicts.join("; ") : String(source.contradicts)}</p>}
      </li>)}
    </ul>
    {[ ["Uncertainties", evidence.uncertainties], ["Contradictions", evidence.contradictions] ].map(([label, items]) => Array.isArray(items) && items.length > 0 && <div key={label}><h4>{label}</h4><ul>{items.map((item, index) => <li key={index}>{String(item)}</li>)}</ul></div>)}
  </section>;
}

function UpdateTarget({ candidate, target, onChange }) {
  const [records, setRecords] = useState(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    fetchIncidentSnapshot({ signal: controller.signal })
      .then(({ items }) => setRecords(items))
      .catch((failure) => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, []);
  const matches = records ? filterIncidents(records, query).slice(0, 40) : [];
  const selectedRecord = records?.find((incident) => incident.id === target);
  if (selectedRecord && !matches.some((incident) => incident.id === target)) matches.unshift(selectedRecord);
  const link = target ? new URL(`/?incident=${encodeURIComponent(target)}`, import.meta.env.VITE_PUBLIC_ORIGIN || window.location.origin).href : null;
  return <div className="incident-form-grid">
    {candidate.suggestedIncidentId && <p className="wide-field admin-help">{candidate.evidence?.matchReason || "The report may concern an incident already in the archive."}</p>}
    <label>Search published incidents<input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Title, organisation or year" /></label>
    <label>Incident to update
      <select value={(selectedRecord || !records) ? target : ""} onChange={(event) => onChange(event.target.value)} disabled={!records || Boolean(error)}>
        <option value="">{records ? (matches.length ? "Select an incident" : "No matches") : "Loading incidents…"}</option>
        {matches.map((incident) => <option key={incident.id} value={incident.id}>{[incident.title, incident.year, incident.incidentCountry].filter(Boolean).join(" · ")}</option>)}
      </select>
    </label>
    {error && <p className="inline-error wide-field" role="alert">Could not load the incident archive. {error}</p>}
    {link && selectedRecord && <a className="wide-field" href={link} target="_blank" rel="noopener noreferrer">Open the existing incident</a>}
  </div>;
}

export function AdminPanel({ client, canCreate, onChanged }) {
  const queueRequest = useRef(null);
  const [candidates, setCandidates] = useState([]);
  const [queueStatus, setQueueStatus] = useState("pending");
  const [catalogs, setCatalogs] = useState({});
  const [catalogError, setCatalogError] = useState("");
  const [selected, setSelected] = useState(null);
  const [values, setValues] = useState({});
  const [busy, setBusy] = useState(false);
  const [showRejectForm, setShowRejectForm] = useState(false);
  const [rejectionReason, setRejectionReason] = useState("");
  const [rejectionNotes, setRejectionNotes] = useState("");
  const [publicationMode, setPublicationMode] = useState("new");
  const [updateTarget, setUpdateTarget] = useState("");
  const [updateSummary, setUpdateSummary] = useState("");
  const [independentSources, setIndependentSources] = useState(false);
  const [distinctIncident, setDistinctIncident] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => {
    if (!client) return;
    const controller = new AbortController();
    client.catalogs({ signal: controller.signal }).then((result) => {
      if (!controller.signal.aborted) {
        setCatalogs(result.catalogs ?? {});
        setCatalogError("");
      }
    }).catch(() => {
      if (!controller.signal.aborted) setCatalogError("Workbook suggestions are unavailable. You can still enter the reported values.");
    });
    return () => controller.abort();
  }, [client]);
  useEffect(() => () => queueRequest.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController();
    queueRequest.current = controller;
    setBusy(true);
    client.candidates({ signal: controller.signal }).then(async (records) => {
      if (controller.signal.aborted) return;
      setCandidates(records);
      const requested = await requestedReviewCandidate(client, records, window.location.search);
      if (requested && !controller.signal.aborted) choose(requested);
    }).catch((failure) => {
      if (!controller.signal.aborted) setError(failure.message);
    }).finally(() => {
      if (!controller.signal.aborted) setBusy(false);
    });
    return () => controller.abort();
  }, [client]);
  function choose(candidate) {
    setSelected(candidate);
    setShowRejectForm(false);
    setRejectionReason("");
    setRejectionNotes("");
    setPublicationMode(candidate?.isPotentialUpdate || candidate?.suggestedIncidentId ? "update" : "new");
    setUpdateTarget(candidate?.suggestedIncidentId || "");
    setUpdateSummary(candidate?.evidence?.materialUpdate?.summary || candidate?.summary || "");
    setIndependentSources(false);
    setDistinctIncident(false);
    setValues(candidateDraft(candidate));
    setMessage("");
    setError("");
  }
  async function changeQueue(status) {
    queueRequest.current?.abort();
    const controller = new AbortController();
    queueRequest.current = controller;
    setBusy(true);
    setError("");
    try {
      const rows = await client.candidates({ status, signal: controller.signal });
      if (controller.signal.aborted) return;
      setCandidates(rows);
      setQueueStatus(status);
      choose(null);
      setValues({});
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure.message);
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  async function refreshAfterSave() {
    try { setCandidates(await client.candidates({ status: queueStatus })); }
    catch (failure) { setError(`Your change was saved, but the report list could not refresh. ${failure.message}`); }
  }
  async function publish(event) {
    event.preventDefault();
    if (busy || (selected ? selected.status !== "pending" : !canCreate)) return;
    if (showRejectForm) return setError(REJECTION_PUBLICATION_BLOCK);
    setError("");
    setMessage("");
    let payload;
    const isUpdate = Boolean(selected && publicationMode === "update");
    if (!independentSources) return setError("Confirm that at least two independent sources support this publication.");
    if (isUpdate && (!updateTarget || !updateSummary.trim())) return setError("Choose the incident to update and describe the new development.");
    if (!isUpdate && needsDistinctConfirmation && !distinctIncident) return setError("Confirm that this is a separate incident.");
    try {
      const input = isUpdate ? {
        title: selected.title, summary: updateSummary,
        sourceUrl: values.sourceUrl, referenceUrlsText: values.referenceUrlsText,
        referenceSources: values.referenceSources,
      } : values;
      payload = reviewedIncidentPayload(input);
      payload.independentSourcesConfirmed = true;
      if (isUpdate) payload.updateIncidentId = updateTarget;
      if (!isUpdate && needsDistinctConfirmation) payload.distinctIncidentConfirmed = true;
    }
    catch (failure) { return setError(failure.message); }
    const hasLat = payload.latitude !== null;
    setBusy(true);
    try {
      const result = await submitReviewedIncident(client, { candidate: selected, payload, rejectionOpen: showRejectForm });
      setMessage(
        isUpdate ? "Update added to the existing incident." : hasLat
          ? "Incident saved. The map is updating."
          : "Incident published without a map point. Add verified coordinates before it can appear on the map.",
      );
      setSelected(null);
      setValues({});
      if (selected) setCandidates((rows) => rows.filter((row) => row.id !== selected.id));
      // Reset the completed report before any fallible refresh can leave stale actions.
      onChanged?.(result);
      await refreshAfterSave();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }

  async function rejectCandidate() {
    if (!selected || selected.status !== "pending" || !rejectionReason || !rejectionNotes.trim() || busy) return;

    setBusy(true);
    setError("");
    setMessage("");

    try {
      await client.reject(selected.id, {
        reason: rejectionReason,
        notes: rejectionNotes.trim(),
      });

      setCandidates((rows) => rows.filter((row) => row.id !== selected.id));
      setSelected(null);
      setValues({});
      setShowRejectForm(false);
      setRejectionReason("");
      setRejectionNotes("");

      setMessage("Report rejected. Your explanation is available for prompt improvement.");
      await refreshAfterSave();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }

  const readOnly = Boolean(selected && selected.status !== "pending");
  const updating = Boolean(selected && !readOnly && publicationMode === "update");
  const needsDistinctConfirmation = Boolean(selected?.suggestedIncidentId || selected?.isPotentialUpdate);
  const changeValue = (key, value) => {
    setValues((previous) => ({ ...previous, [key]: value }));
    if (key === "sourceUrl" || key === "referenceUrlsText") setIndependentSources(false);
  };
  return (
    <div className="admin-panel admin-review">
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="success-message" role="status">
          {message}
        </p>
      )}
      {!(selected || Object.keys(values).length > 0) && <section className="candidate-queue workspace-card" aria-label="Incoming reports">
        <div className="review-queue-toolbar">
          <label>Report status
            <select value={queueStatus} disabled={busy} onChange={(event) => changeQueue(event.target.value)}>
              <option value="pending">Awaiting review</option>
              <option value="rejected">Rejected — feedback history</option>
              <option value="approved">Approved</option>
            </select>
          </label>
          <button type="button" className="secondary-action" disabled={busy} onClick={() => changeQueue(queueStatus)}>Refresh reports</button>
        </div>
        <p className="workspace-muted" aria-live="polite">{busy ? "Updating reports…" : `${candidates.length} ${queueStatus} reports`}</p>
        {busy && !candidates.length ? null : !candidates.length ? (
          <p>{queueStatus === "pending" ? "No reports awaiting review." : `No ${queueStatus} reports.`}</p>
        ) : (
          <ul>
            {candidates.map((candidate) => (
              <li key={candidate.id}>
                <strong>{candidate.title}</strong>
                <p>{candidate.summary}</p>
                {candidate.origin === "ai" && <p className="admin-help">AI-discovered report · {candidate.verificationStatus?.replaceAll("_", " ") || "requires review"}</p>}
                {candidate.isPotentialUpdate && <p className="admin-help">Possible update to a published incident</p>}
                {candidate.status === "rejected" && <p><strong>{rejectionLabels[candidate.rejectionReason] || candidate.rejectionReason || "Rejected"}</strong>{candidate.rejectionNotes ? ` — ${candidate.rejectionNotes}` : ""}</p>}
                {safeReferenceUrl(candidate.sourceUrl) && (
                  <a
                    href={safeReferenceUrl(candidate.sourceUrl)}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Read source
                  </a>
                )}
                <button type="button" disabled={busy} onClick={() => choose(candidate)}>
                  {candidate.status === "pending" ? "Review" : "View report"}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>}
      {canCreate && !selected && Object.keys(values).length === 0 && <button
        type="button"
        className="secondary-action"
        disabled={busy}
        onClick={() => choose(null)}
      >
        Add a new incident
      </button>}
      {(selected || Object.keys(values).length > 0) && (
        <form className="incident-form workspace-card" onSubmit={publish}>
          <button type="button" className="secondary-action" disabled={busy} onClick={() => { choose(null); setValues({}); }}>Back to queue</button>
          <h2>{readOnly ? `${selected.status === "rejected" ? "Rejected" : "Approved"} report` : selected ? "Verify and publish report" : "New incident"}</h2>
          {readOnly && <p className="admin-help">This report is read-only. Completed reviews cannot be published again from this queue.</p>}
          {selected?.status === "approved" && <p className="admin-help">The fields below show the original source proposal. {selected.incidentId && <a href={new URL(`/?incident=${encodeURIComponent(selected.incidentId)}`, import.meta.env.VITE_PUBLIC_ORIGIN || window.location.origin).href} target="_blank" rel="noopener noreferrer">View the published record with the reviewer’s changes.</a>}</p>}
          {selected?.status === "rejected" && <section className="review-feedback"><h3>Saved rejection feedback</h3><p><strong>{rejectionLabels[selected.rejectionReason] || selected.rejectionReason || "Reason not recorded"}</strong></p><p>{selected.rejectionNotes || "No additional explanation."}</p></section>}
          {selected && <EvidenceDetails candidate={selected} />}
          {!updating && <p>
            Fields follow the MCAD workbook. Leave unknown dates and locations blank. Publish confirmed facts only; do not guess coordinates from a country.
          </p>}
          {catalogError && <p className="admin-help">{catalogError}</p>}
          {Object.entries(catalogs).map(([name, entries]) => <datalist id={`mcad-catalog-${name}`} key={name}>
            {Array.isArray(entries) && entries.map((entry, index) => <option value={entry.value} key={`${entry.id || entry.value}-${index}`} />)}
          </datalist>)}
          <fieldset className="review-fields" disabled={readOnly || busy}>
          <legend className="visually-hidden">Incident data</legend>
          {selected && !readOnly && <label>Publication action
            <select value={publicationMode} onChange={(event) => {
              setPublicationMode(event.target.value);
              setIndependentSources(false);
            }}>
              <option value="new">Publish as a new incident</option>
              <option value="update">Update a published incident</option>
            </select>
          </label>}
          {updating && <>
            <UpdateTarget key={selected.id} candidate={selected} target={updateTarget} onChange={setUpdateTarget} />
            <label>New development<textarea rows={4} value={updateSummary} onChange={(event) => setUpdateSummary(event.target.value)} /></label>
          </>}
          <div className="incident-form-grid">
            {fields.filter(([key]) => !updating || key === "sourceUrl" || key === "referenceUrlsText").map(([key, label, required, type = "text"]) => (
              <label
                key={key}
                className={type === "textarea" ? "wide-field" : ""}
              >
                {label}
                {required ? " *" : ""}
                {type === "textarea" ? (
                  <textarea
                    value={values[key] ?? ""}
                    onChange={(event) =>
                      changeValue(key, event.target.value)
                    }
                    rows={4}
                  />
                ) : key === "locationPrecision" ? (
                  <select
                    value={values[key] || "unknown"}
                    onChange={(event) =>
                      changeValue(key, event.target.value)
                    }
                  >
                    <option value="unknown">Unknown / not mapped</option>
                    <option value="exact">Exact site</option>
                    <option value="city">City / port approximation</option>
                    <option value="region">Region approximation</option>
                    <option value="country">Country approximation</option>
                    <option value="source_coordinate">
                      Source coordinates, precision unspecified
                    </option>
                  </select>
                ) : (
                  <input
                    type={type}
                    step={type === "number" ? ["latitude", "longitude"].includes(key) ? "any" : "1" : undefined}
                    list={catalogFields[key] ? `mcad-catalog-${catalogFields[key]}` : undefined}
                    value={values[key] ?? ""}
                    required={required}
                    onChange={(event) =>
                      changeValue(key, event.target.value)
                    }
                  />
                )}
              </label>
            ))}
          </div>
          {!updating && <details>
            <summary>Additional classification</summary>
            <div className="incident-form-grid">
              {classification.map(([key, label]) => (
                <label key={key}>
                  {label}
                  {catalogFields[key] ? <input
                    list={`mcad-catalog-${catalogFields[key]}`}
                    value={values[key] ?? ""}
                    onChange={(event) => setValues({ ...values, [key]: event.target.value })}
                  /> : <textarea
                    rows={2}
                    value={values[key] ?? ""}
                    onChange={(event) =>
                      setValues({ ...values, [key]: event.target.value })
                    }
                  />}
                </label>
              ))}
            </div>
          </details>}
          {!readOnly && <>
            <label className="review-confirmation">
              <input type="checkbox" checked={independentSources} onChange={(event) => setIndependentSources(event.target.checked)} />
              <span>I checked that two independent sources substantiate {updating ? "this update" : "this incident"}.</span>
            </label>
            {!updating && needsDistinctConfirmation && <label className="review-confirmation">
              <input type="checkbox" checked={distinctIncident} onChange={(event) => setDistinctIncident(event.target.checked)} />
              <span>I verified that this is a separate incident.</span>
            </label>}
          </>}
          </fieldset>
          {!readOnly && <>
          <button className="primary-action" type="submit" disabled={busy || showRejectForm || !independentSources || (updating ? !updateTarget || !updateSummary.trim() : needsDistinctConfirmation && !distinctIncident)}>
            {busy ? "Saving…" : updating ? "Publish incident update" : "Publish verified incident"}
          </button>
          {selected && (
            <>
              <button
                type="button"
                className="secondary-action"
                disabled={busy}
                onClick={() => setShowRejectForm(!showRejectForm)}
              >
                {showRejectForm ? "Cancel rejection" : "Reject incident"}
              </button>

              {showRejectForm && (
                <div className="rejection-form">
                  <h3>Reject incident</h3>

                  <label>
                    Reason for rejection *
                    <select
                      value={rejectionReason}
                      onChange={(event) =>
                        setRejectionReason(event.target.value)
                      }
                    >
                      <option value="">Select a reason</option>
                      <option value="not_maritime">
                        Not maritime-related
                      </option>
                      <option value="not_cyber">
                        Not a cybersecurity incident
                      </option>
                      <option value="duplicate">
                        Duplicate incident
                      </option>
                      <option value="insufficient_evidence">
                        Insufficient evidence
                      </option>
                      <option value="incorrect_information">
                        Incorrect information
                      </option>
                      <option value="other">
                        Other
                      </option>
                    </select>
                  </label>

                  <label>
                    Explanation for rejection *
                    <textarea
                      rows={3}
                      maxLength={2000}
                      value={rejectionNotes}
                      onChange={(event) =>
                        setRejectionNotes(event.target.value)
                      }
                      placeholder="Explain why this incident was rejected..."
                    />
                  </label>

                  <button
                    type="button"
                    className="secondary-action"
                    disabled={busy || !rejectionReason || !rejectionNotes.trim()}
                    onClick={rejectCandidate}
                  >
                    {busy ? "Rejecting..." : "Confirm rejection"}
                  </button>
                </div>
              )}
            </>
          )}
          </>}
          <button type="button" className="secondary-action" disabled={busy} onClick={() => { choose(null); setValues({}); }}>Close report</button>
        </form>
      )}
    </div>
  );
}
