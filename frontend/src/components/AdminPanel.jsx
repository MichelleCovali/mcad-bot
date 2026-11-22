import { useEffect, useRef, useState } from "react";
import { safeReferenceUrl } from "../data/incidents.js";
import {
  candidateDraft,
  reviewedIncidentPayload,
  submitReviewedIncident,
  requestedReviewCandidate,
  candidateNeedsRelationshipReview,
} from "../data/review.js";
import { CandidateRelationship } from "./CandidateRelationship.jsx";
import { IncidentRevisionHistory } from "./IncidentRevisionHistory.jsx";
import { IncidentUpdateReview } from "./IncidentUpdateReview.jsx";

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
  not_maritime: "Not maritime-related",
  not_cyber: "Not a cybersecurity incident",
  duplicate: "Duplicate incident",
  insufficient_evidence: "Insufficient evidence",
  incorrect_information: "Incorrect information",
  old_news: "Old news",
  other: "Other",
};
const catalogFields = {
  incidentCountry: "countries",
  victimCountry: "countries",
  attackerCountry: "countries",
  victimType: "victim_types",
  impactArea: "impact_areas",
  method: "methods",
};

function EvidenceDetails({ candidate }) {
  const evidence = candidate.evidence;
  if (
    !evidence ||
    typeof evidence !== "object" ||
    !Object.keys(evidence).length
  )
    return null;
  const statuses = {
    supported: "Sources found — human review required",
    needs_review: "Further evidence needed",
    contradicted: "Conflicting reports",
  };
  return (
    <section className="candidate-evidence">
      <h3>Source checks</h3>
      <p>
        <strong>
          {statuses[candidate.verificationStatus] ||
            "Not independently verified"}
        </strong>
      </p>
      {typeof evidence.assessment === "string" && <p>{evidence.assessment}</p>}
      <p className="admin-help">
        Source independence and supporting statements below are model
        assessments. Open the original reports and check them before publishing.
      </p>
      <dl className="detail-fields">
        {[
          ["Checked", evidence.checkedAt],
          ["First reported", evidence.firstReportedAt],
          ["Event date", evidence.eventDate],
        ].map(
          ([label, value]) =>
            value && (
              <div className="detail-field" key={label}>
                <dt>{label}</dt>
                <dd>{String(value)}</dd>
              </div>
            ),
        )}
      </dl>
      <ul>
        {Array.isArray(evidence.sources) &&
          evidence.sources.map((source, index) => (
            <li key={`${source.url}-${index}`}>
              {safeReferenceUrl(source.url) ? (
                <a
                  href={safeReferenceUrl(source.url)}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {source.title || source.url}
                </a>
              ) : (
                <strong>Source link unavailable</strong>
              )}
              <p>
                {source.publisher || "Publisher unknown"} · Independence group:{" "}
                {source.independenceGroup || "unknown"}
                {source.isPrimary === true ? " · Reported primary source" : ""}
              </p>
              {source.snippet && (
                <blockquote>{String(source.snippet)}</blockquote>
              )}
              {source.supports && (
                <p>
                  Supports:{" "}
                  {Array.isArray(source.supports)
                    ? source.supports.join("; ")
                    : String(source.supports)}
                </p>
              )}
              {source.contradicts && (
                <p>
                  Contradicts:{" "}
                  {Array.isArray(source.contradicts)
                    ? source.contradicts.join("; ")
                    : String(source.contradicts)}
                </p>
              )}
            </li>
          ))}
      </ul>
      {[
        ["Uncertainties", evidence.uncertainties],
        ["Contradictions", evidence.contradictions],
      ].map(
        ([label, items]) =>
          Array.isArray(items) &&
          items.length > 0 && (
            <div key={label}>
              <h4>{label}</h4>
              <ul>
                {items.map((item, index) => (
                  <li key={index}>{String(item)}</li>
                ))}
              </ul>
            </div>
          ),
      )}
    </section>
  );
}
export function AdminPanel({ client, canCreate, onChanged }) {
  const queueRequest = useRef(null);
  const draftCache = useRef(new Map());
  const reportHeading = useRef(null);
  const actionHeading = useRef(null);
  const queueHeading = useRef(null);
  const [action, setAction] = useState("");
  const [independentSources, setIndependentSources] = useState(false);
  const [updateReason, setUpdateReason] = useState("");
  const [queueLoading, setQueueLoading] = useState(false);
  const [refreshError, setRefreshError] = useState("");
  const [savedIncidentId, setSavedIncidentId] = useState(null);
  const [showSavedHistory, setShowSavedHistory] = useState(false);
  const [candidates, setCandidates] = useState([]);
  const [queueStatus, setQueueStatus] = useState("pending");
  const [catalogs, setCatalogs] = useState({});
  const [catalogError, setCatalogError] = useState("");
  const [selected, setSelected] = useState(null);
  const [values, setValues] = useState({});
  const [busy, setBusy] = useState(false);

  const [rejectionReason, setRejectionReason] = useState("");
  const [rejectionNotes, setRejectionNotes] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [relationshipReason, setRelationshipReason] = useState("");
  useEffect(() => {
    if (!client) return;
    const controller = new AbortController();
    client
      .catalogs({ signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) {
          setCatalogs(result.catalogs ?? {});
          setCatalogError("");
        }
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setCatalogError(
            "Workbook suggestions are unavailable. You can still enter the reported values.",
          );
      });
    return () => controller.abort();
  }, [client]);
  useEffect(() => () => queueRequest.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController();
    queueRequest.current = controller;
    setQueueLoading(true);
    setBusy(true);
    client.candidates({ signal: controller.signal }).then(async (records) => {
      if (controller.signal.aborted) return;
      setCandidates(records);
      const requested = await requestedReviewCandidate(client, records, window.location.search);
      if (requested && !controller.signal.aborted) choose(requested);
    }).catch((failure) => {
      if (!controller.signal.aborted) setError(failure.message);
    }).finally(() => {
      if (!controller.signal.aborted) {
        setBusy(false);
        setQueueLoading(false);
      }
    });
    return () => controller.abort();
  }, [client]);

  useEffect(() => {
    if (selected) reportHeading.current?.focus();
    else if (!action) queueHeading.current?.focus();
  }, [selected?.id]);

  useEffect(() => {
    if (action) actionHeading.current?.focus();
  }, [action, selected?.id]);

  function rememberDraft() {
    if (!action || (selected && selected.status !== "pending")) return;
    const key = selected?.id ?? "manual";
    draftCache.current.set(key, {
      ...draftCache.current.get(key),
      [action]: { values, relationshipReason, updateReason, rejectionReason, rejectionNotes },
    });
  }

  function loadAction(next, candidate) {
    const draft = draftCache.current.get(candidate?.id ?? "manual")?.[next];
    setAction(next);
    setIndependentSources(false);
    setValues(draft?.values ?? (next ? candidateDraft(candidate) : {}));
    setRelationshipReason(draft?.relationshipReason ?? "");
    setUpdateReason(draft?.updateReason ?? "");
    setRejectionReason(draft?.rejectionReason ?? "");
    setRejectionNotes(draft?.rejectionNotes ?? "");
  }

  function choose(candidate, next = "") {
    rememberDraft();
    setSelected(candidate);
    loadAction(next, candidate);
    setShowSavedHistory(false);
    setSavedIncidentId(null);
    setMessage("");
    setError("");
  }

  function chooseAction(next) {
    if (next === action) return;
    rememberDraft();
    loadAction(next, selected);
    setError("");
  }

  function reloadReport(report) {
    if (report.status === "pending") rememberDraft();
    setSelected(report);
    setCandidates((rows) => rows.flatMap((row) => {
      if (row.id !== report.id) return [row];
      return report.status === queueStatus ? [report] : [];
    }));
    if (report.status !== "pending" || report.relationshipType === "new_incident") {
      draftCache.current.delete(report.id);
      loadAction("", report);
      reportHeading.current?.focus();
    }
  }

  function completeReview(result, text, refreshPublic) {
    draftCache.current.delete(selected?.id ?? "manual");
    setSelected(null);
    loadAction("", null);
    setError("");
    setMessage(text);
    setSavedIncidentId(refreshPublic ? result.id : null);
    if (selected) setCandidates((rows) => rows.filter((row) => row.id !== selected.id));
    if (refreshPublic) onChanged?.();
    return refreshAfterSave();
  }

  async function changeQueue(status) {
    setQueueLoading(true);
    setRefreshError("");
    queueRequest.current?.abort();
    const controller = new AbortController();
    queueRequest.current = controller;
    setBusy(true);
    setError("");
    try {
      const rows = await client.candidates({
        status,
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      setCandidates(rows);
      setQueueStatus(status);
      choose(null);
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure.message);
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
        setQueueLoading(false);
      }
    }
  }
  async function refreshAfterSave(restoreFocus = false) {
    queueRequest.current?.abort();
    const controller = new AbortController();
    queueRequest.current = controller;
    try {
      const rows = await client.candidates({ status: queueStatus, signal: controller.signal });
      if (!controller.signal.aborted) {
        if (restoreFocus === true) queueHeading.current?.focus();
        setCandidates(rows);
        setRefreshError("");
      }
    } catch (failure) {
      if (!controller.signal.aborted) {
        setRefreshError(`The report list could not refresh. ${failure.message}`);
      }
    }
  }

  async function markAsSeparateIncident() {
    if (
      busy ||
      action !== "publish" ||
      !selected ||
      selected.status !== "pending" ||
      !relationshipBlocked ||
      !relationshipReason.trim()
    )
      return;

    setBusy(true);
    setError("");
    setMessage("");

    try {
      const updated = await client.resolveRelationship(
        selected.id,
        "new_incident",
        relationshipReason.trim(),
      );

      setSelected(updated);
      setCandidates((rows) =>
        rows.map((row) => (row.id === updated.id ? updated : row)),
      );
      setRelationshipReason("");
      setMessage(
        "Marked as a separate incident. Nothing has been published. " +
          "Review the evidence and incident details before publishing.",
      );
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }

  function finishIncidentUpdate(incident) {
    return completeReview(incident, `Updated ${incident.title}. Earlier sources were preserved.`, true);
  }

  async function publish(event) {
    event.preventDefault();
    if (busy || !independentSources || action !== "publish" || (selected ? selected.status !== "pending" : !canCreate)) return;
    setError("");
    setMessage("");
    let payload;
    try {
      payload = reviewedIncidentPayload(values);
      payload.independentSourcesConfirmed = independentSources;
      if (selected?.relationshipType === "new_incident") payload.distinctIncidentConfirmed = true;
    } catch (failure) {
      return setError(failure.message);
    }
    const hasLat = payload.latitude !== null;
    setBusy(true);
    try {
      const result = await submitReviewedIncident(client, {
        candidate: selected,
        payload,
        rejectionOpen: false,
      });
      await completeReview(
        result,
        hasLat ? `Published ${result.title}.` : `Published ${result.title} without a map point.`,
        true,
      );
    } catch (failure) {
      if (selected && failure.code === "report_resolved") {
        try {
          const report = await client.candidate(selected.id);
          reloadReport(report);
          setMessage("Showing the saved review outcome.");
        } catch (reloadFailure) {
          choose(null);
          setError(`${failure.message} Could not load the saved outcome. ${reloadFailure.message}`);
        }
      } else {
        setError(failure.message);
      }
    } finally {
      setBusy(false);
    }
  }

  async function rejectCandidate(event) {
    event.preventDefault();
    if (!selected || selected.status !== "pending" || !rejectionReason || !rejectionNotes.trim() || busy)
      return;

    setBusy(true);
    setError("");
    setMessage("");

    try {
      await client.reject(selected.id, {
        reason: rejectionReason,
        notes: rejectionNotes,
      });

      await completeReview(null, `Rejected ${selected.title}. Feedback saved.`, false);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }

  const readOnly = Boolean(selected && selected.status !== "pending");
  const relationshipBlocked = candidateNeedsRelationshipReview(selected) ||
    Boolean(selected?.isPotentialUpdate && selected.relationshipType !== "new_incident" && selected.status === "pending");
  return (
    <div className="admin-panel">
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      <div role="status">
        {message && <p className="success-message">{message}</p>}
      </div>
      {refreshError && (
        <p className="inline-error" role="alert">
          {refreshError} <button type="button" onClick={() => refreshAfterSave(true)}>Retry report refresh</button>
        </p>
      )}
      {savedIncidentId && (
        <p>
          <a href={`/?incident=${encodeURIComponent(savedIncidentId)}`} target="_blank" rel="noopener noreferrer">View saved incident</a>
        </p>
      )}
        <div className={selected || action ? "review-workspace has-report" : "review-workspace"}>
          <section className="candidate-queue" aria-labelledby="review-queue-title">
            <h2 id="review-queue-title" ref={queueHeading} tabIndex={-1}>Review queue</h2>
            <div className="review-queue-toolbar">
              <label>
                Report status
                <select
                  value={queueStatus}
                  disabled={busy}
                  onChange={(event) => changeQueue(event.target.value)}
                >
                  <option value="pending">Awaiting review</option>
                  <option value="rejected">Rejected — feedback history</option>
                  <option value="approved">Approved</option>
                </select>
              </label>
              <button
                type="button"
                className="secondary-action"
                disabled={busy}
                onClick={() => changeQueue(queueStatus)}
              >
                Refresh reports
              </button>
            </div>
            {queueLoading ? <p role="status">Loading reports…</p> : !candidates.length ? (
              <p>
                {queueStatus === "pending"
                  ? "No reports awaiting review."
                  : `No ${queueStatus} reports.`}
              </p>
            ) : (
              <ul>
                {candidates.map((candidate) => (
                  <li key={candidate.id}>
                    <strong>{candidate.title}</strong>
                    <p>{candidate.summary}</p>
                    {candidate.origin === "ai" && (
                      <p className="admin-help">
                        AI-discovered report ·{" "}
                        {candidate.verificationStatus?.replaceAll("_", " ") ||
                          "requires review"}
                      </p>
                    )}
                    {candidate.status === "rejected" && (
                      <p>
                        <strong>
                          {rejectionLabels[candidate.rejectionReason] ||
                            candidate.rejectionReason ||
                            "Rejected"}
                        </strong>
                        {candidate.rejectionNotes
                          ? ` — ${candidate.rejectionNotes}`
                          : ""}
                      </p>
                    )}
                    {safeReferenceUrl(candidate.sourceUrl) && (
                      <a
                        href={safeReferenceUrl(candidate.sourceUrl)}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        Read source
                      </a>
                    )}
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => choose(candidate)}
                    >
                      {candidate.status === "pending" ? "Review" : "View report"}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {canCreate && <button type="button" className="secondary-action" disabled={busy}
              onClick={() => choose(null, "publish")}>Add a new incident</button>}
          </section>
          {(selected || action) && (
            <article className="review-report">
              <button type="button" className="secondary-action" disabled={busy} onClick={() => choose(null)}>
                Back to queue
              </button>
              {selected && (
                <>
                  <h2 ref={reportHeading} tabIndex={-1}>{selected.title}</h2>
                  <p>{selected.status === "pending" ? "Awaiting review" : selected.status === "rejected" ? "Rejected" : selected.relationshipType === "update" ? "Applied as an update" : "Approved"}</p>
                  <p>{selected.summary || "No summary provided."}</p>
                  {safeReferenceUrl(selected.sourceUrl) && (
                    <p><a href={safeReferenceUrl(selected.sourceUrl)} target="_blank" rel="noopener noreferrer">Read main source</a></p>
                  )}
                  {[
                    ["Uncertainties", selected.evidence?.uncertainties],
                    ["Conflicting claims", selected.evidence?.contradictions],
                  ].map(([label, items]) => Array.isArray(items) && items.length > 0 && (
                    <div key={label}>
                      <h3>{label}</h3>
                      <ul>{items.map((item, index) => <li key={index}>{String(item)}</li>)}</ul>
                    </div>
                  ))}
                  {selected.verificationStatus === "contradicted" && <p>Source claims conflict. Review the evidence.</p>}
                  {selected.verificationStatus === "needs_review" && <p>Further evidence is needed.</p>}
                  {(relationshipBlocked || selected.relationshipReason) && <CandidateRelationship candidate={{
                    ...selected,
                    relatedIncidentId: selected.relatedIncidentId ?? selected.suggestedIncidentId,
                    relationshipReason: selected.relationshipReason || selected.evidence?.matchReason,
                  }} />}
                  {selected.evidence && Object.keys(selected.evidence).length > 0 && (
                    <details><summary>Sources and evidence</summary><EvidenceDetails candidate={selected} /></details>
                  )}
                  {readOnly && selected.proposedFields && Object.keys(selected.proposedFields).length > 0 && (
                    <details>
                      <summary>Original proposed fields</summary>
                      <dl className="detail-fields">
                        {[...fields, ...classification].map(([key, label]) => {
                          const value = selected.proposedFields[key];
                          return typeof value === "string" || typeof value === "number"
                            ? <div className="detail-field" key={key}><dt>{label}</dt><dd>{value}</dd></div>
                            : null;
                        })}
                      </dl>
                    </details>
                  )}
                  {selected.status === "rejected" && (
                    <section className="review-feedback">
                      <h3>Saved rejection feedback</h3>
                      <p>{rejectionLabels[selected.rejectionReason] || selected.rejectionReason || "Reason not recorded"}</p>
                      {selected.rejectionNotes && <p>{selected.rejectionNotes}</p>}
                    </section>
                  )}
                  {selected.status === "approved" && selected.incidentId && (
                    <div className="admin-actions">
                      <a href={`/?incident=${encodeURIComponent(selected.incidentId)}`} target="_blank" rel="noopener noreferrer">View published record</a>
                      {canCreate && <button type="button" className="secondary-action" aria-expanded={showSavedHistory}
                        onClick={() => setShowSavedHistory(!showSavedHistory)}>
                        {showSavedHistory ? "Hide revision history" : "Revision history"}
                      </button>}
                    </div>
                  )}
                  {showSavedHistory && selected.incidentId && (
                    <IncidentRevisionHistory client={client} incidentId={selected.incidentId} />
                  )}
                  {selected.status === "pending" && (
                    <div className="admin-actions" aria-label="Review decision" role="group">
                      {[
                        ["publish", "Publish a new incident"],
                        ["update", "Update an existing incident"],
                        ["reject", "Reject the report"],
                      ].filter(([key]) => key !== "update" ||
                        ["unclassified", "possible_update", "possible_duplicate"].includes(selected.relationshipType ?? "unclassified")
                      ).map(([key, label]) => (
                        <button key={key} type="button" className="secondary-action" aria-pressed={action === key}
                          disabled={busy} onClick={() => chooseAction(key)}>{label}</button>
                      ))}
                    </div>
                  )}
                </>
              )}
              {!readOnly && action === "update" && selected && (
                <section>
                  <h3 ref={actionHeading} tabIndex={-1}>Update an existing incident</h3>
                  <IncidentUpdateReview key={selected.id} client={client} candidate={selected}
                    draft={values} onDraftChange={setValues} reason={updateReason} onReasonChange={setUpdateReason}
                    canViewHistory={canCreate} disabled={busy} onBusyChange={setBusy} onApplied={finishIncidentUpdate}
                    onReportReloaded={reloadReport} />
                </section>
              )}
              {!readOnly && action === "reject" && selected && (
                <form className="rejection-form" onSubmit={rejectCandidate}>
                  <h3 ref={actionHeading} tabIndex={-1}>Reject the report</h3>
                  <label>Reason for rejection
                    <select value={rejectionReason} onChange={(event) => setRejectionReason(event.target.value)} required disabled={busy}>
                      <option value="">Select a reason</option>
                      {Object.entries(rejectionLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                    </select>
                  </label>
                  <label>Explain the rejection
                    <textarea rows={3} required maxLength={2000} value={rejectionNotes} disabled={busy}
                      onChange={(event) => setRejectionNotes(event.target.value)} />
                  </label>
                  <button type="submit" className="primary-action" disabled={busy || !rejectionReason || !rejectionNotes.trim()}>
                    {busy ? "Rejecting…" : "Confirm rejection"}
                  </button>
                </form>
              )}
              {!readOnly && action === "publish" && (
                <form className="incident-form" onSubmit={publish}>
                  <h3 ref={actionHeading} tabIndex={-1}>Publish a new incident</h3>
                  {relationshipBlocked ? (
                    <section className="candidate-evidence">
                      <h3>Does this report describe a separate incident?</h3>
                      <p>
                        Use this action only if you have checked the suggested match and
                        concluded that these are different events. This decision does
                        not publish the report.
                      </p>

                      <label>
                        Reason for treating this as a separate incident
                        <textarea
                          value={relationshipReason}
                          onChange={(event) =>
                            setRelationshipReason(event.target.value)
                          }
                          maxLength={2000}
                          rows={3}
                          disabled={busy}
                        />
                      </label>

                      <button
                        type="button"
                        className="secondary-action"
                        disabled={busy || !relationshipReason.trim()}
                        onClick={markAsSeparateIncident}
                      >
                        Treat as separate incident
                      </button>
                    </section>
                  ) : (
                  <>
                  <p>
                    Fields follow the MCAD workbook. Leave unknown dates and locations
                    blank. Publish confirmed facts only; do not guess coordinates from a
                    country.
                  </p>
                  {catalogError && <p className="admin-help">{catalogError}</p>}
                  {Object.entries(catalogs).map(([name, entries]) => (
                    <datalist id={`mcad-catalog-${name}`} key={name}>
                      {Array.isArray(entries) &&
                        entries.map((entry, index) => (
                          <option
                            value={entry.value}
                            key={`${entry.id || entry.value}-${index}`}
                          />
                        ))}
                    </datalist>
                  ))}
                  <fieldset className="review-fields" disabled={readOnly || busy}>
                    <legend className="visually-hidden">Incident data</legend>
                    <div className="incident-form-grid">
                      {fields.map(([key, label, required, type = "text"]) => (
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
                                setValues({ ...values, [key]: event.target.value })
                              }
                              rows={4}
                            />
                          ) : key === "locationPrecision" ? (
                            <select
                              value={values[key] || "unknown"}
                              onChange={(event) =>
                                setValues({ ...values, [key]: event.target.value })
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
                              step={
                                type === "number"
                                  ? ["latitude", "longitude"].includes(key)
                                    ? "any"
                                    : "1"
                                  : undefined
                              }
                              list={
                                catalogFields[key]
                                  ? `mcad-catalog-${catalogFields[key]}`
                                  : undefined
                              }
                              value={values[key] ?? ""}
                              required={required}
                              onChange={(event) =>
                                setValues({ ...values, [key]: event.target.value })
                              }
                            />
                          )}
                        </label>
                      ))}
                    </div>
                    <details>
                      <summary>Additional classification</summary>
                      <div className="incident-form-grid">
                        {classification.map(([key, label]) => (
                          <label key={key}>
                            {label}
                            {catalogFields[key] ? (
                              <input
                                list={`mcad-catalog-${catalogFields[key]}`}
                                value={values[key] ?? ""}
                                onChange={(event) =>
                                  setValues({ ...values, [key]: event.target.value })
                                }
                              />
                            ) : (
                              <textarea
                                rows={2}
                                value={values[key] ?? ""}
                                onChange={(event) =>
                                  setValues({ ...values, [key]: event.target.value })
                                }
                              />
                            )}
                          </label>
                        ))}
                      </div>
                    </details>
                  </fieldset>
                  <label className="review-confirmation">
                    <input type="checkbox" checked={independentSources} disabled={busy}
                      onChange={(event) => setIndependentSources(event.target.checked)} />
                    <span>I checked that two independent sources substantiate this incident.</span>
                  </label>
                  <button className="primary-action" type="submit" disabled={busy || !independentSources}>
                    {busy ? "Publishing…" : "Publish verified incident"}
                  </button>
                  </>
                  )}
                </form>
              )}
            </article>
          )}
        </div>
    </div>
  );
}
