import { useEffect, useRef, useState } from "react";
import { createAdminClient, safeReferenceUrl } from "../data/incidents.js";
import {
  candidateDraft,
  reviewedIncidentPayload,
  REJECTION_PUBLICATION_BLOCK,
  submitReviewedIncident,
  requestedReviewCandidate,
  candidateNeedsRelationshipReview,
} from "../data/review.js";
import { AIMonitorSettings } from "./AIMonitorSettings.jsx";
import { TelegramSettings } from "./TelegramSettings.jsx";
import { CandidateRelationship } from "./CandidateRelationship.jsx";

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
export function AdminPanel({ onChanged }) {
  const tokenInput = useRef(null);
  const fileInput = useRef(null);
  const queueRequest = useRef(null);
  const [token, setToken] = useState("");
  const [client, setClient] = useState(null);
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
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [workbook, setWorkbook] = useState(null);
  const [importReport, setImportReport] = useState(null);
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
  async function login(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const connection = createAdminClient(token);
      const records = await connection.candidates();
      setCandidates(records);
      setQueueStatus("pending");
      setClient(connection);
      setToken("");
      try {
        const requested = await requestedReviewCandidate(
          connection,
          records,
          window.location.search,
        );
        if (requested) choose(requested);
      } catch (failure) {
        setError(`The linked report could not be opened. ${failure.message}`);
      }
    } catch (failure) {
      setError(failure.message);
      tokenInput.current?.focus();
    } finally {
      setBusy(false);
    }
  }
  function choose(candidate) {
    setSelected(candidate);
    setShowRejectForm(false);
    setRejectionReason("");
    setRejectionNotes("");
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
      const rows = await client.candidates({
        status,
        signal: controller.signal,
      });
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
    try {
      setCandidates(await client.candidates({ status: queueStatus }));
    } catch (failure) {
      setError(
        `Your change was saved, but the report list could not refresh. ${failure.message}`,
      );
    }
  }
  async function publish(event) {
    event.preventDefault();
    if (busy || (selected && selected.status !== "pending")) return;
    if (showRejectForm) return setError(REJECTION_PUBLICATION_BLOCK);
    setError("");
    setMessage("");
    let payload;
    try {
      payload = reviewedIncidentPayload(values);
    } catch (failure) {
      return setError(failure.message);
    }
    const hasLat = payload.latitude !== null;
    setBusy(true);
    try {
      const result = await submitReviewedIncident(client, {
        candidate: selected,
        payload,
        rejectionOpen: showRejectForm,
      });
      setMessage(
        hasLat
          ? "Incident saved. The map is updating."
          : "Incident published without a map point. Add verified coordinates before it can appear on the map.",
      );
      setSelected(null);
      setValues({});
      if (selected)
        setCandidates((rows) => rows.filter((row) => row.id !== selected.id));
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
    if (!selected || selected.status !== "pending" || !rejectionReason || busy)
      return;

    setBusy(true);
    setError("");
    setMessage("");

    try {
      await client.reject(selected.id, {
        reason: rejectionReason,
        notes: rejectionNotes,
      });

      setCandidates((rows) => rows.filter((row) => row.id !== selected.id));
      setSelected(null);
      setValues({});
      setShowRejectForm(false);
      setRejectionReason("");
      setRejectionNotes("");

      setMessage("Incident rejected. Feedback saved successfully.");
      await refreshAfterSave();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }

  async function importWorkbook(event) {
    event.preventDefault();
    setError("");
    setMessage("");
    setImportReport(null);
    setBusy(true);
    try {
      const report = await client.importWorkbook(workbook);
      setImportReport(report);
      setWorkbook(null);
      if (fileInput.current) fileInput.current.value = "";
      setMessage(
        "Workbook import complete. Historical records were imported without sending notifications.",
      );
      onChanged?.(report);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }
  const readOnly = Boolean(selected && selected.status !== "pending");
  const relationshipBlocked = candidateNeedsRelationshipReview(selected);
  if (!client)
    return (
      <div className="admin-panel">
        <p>Sign in to review reports and publish verified incidents.</p>
        <form onSubmit={login} className="admin-login">
          <label>
            Administrator access token
            <input
              ref={tokenInput}
              type="password"
              autoComplete="off"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              required
            />
          </label>
          <button className="primary-action" disabled={busy}>
            {busy ? "Connecting…" : "Sign in"}
          </button>
        </form>
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        <p className="admin-help">
          Access is kept only in memory for this open panel. Closing it signs
          you out.
        </p>
      </div>
    );
  return (
    <div className="admin-panel">
      <div className="admin-toolbar">
        <p>
          {candidates.length} {queueStatus} reports
        </p>
        <button
          type="button"
          onClick={() => {
            setClient(null);
            setToken("");
            setValues({});
            setSelected(null);
            setWorkbook(null);
            setImportReport(null);
            setCatalogs({});
            setCatalogError("");
            queueRequest.current?.abort();
          }}
        >
          Sign out
        </button>
      </div>
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
      <AIMonitorSettings client={client} onRunComplete={refreshAfterSave} />
      <TelegramSettings client={client} />
      <details className="workbook-import">
        <summary>Import a historical workbook</summary>
        <p>
          Import an .xlsx workbook of up to 10 MB. Original fields and source
          rows are preserved. This historical import does not send Telegram
          notifications.
        </p>
        <form onSubmit={importWorkbook} className="admin-login">
          <label>
            Excel workbook
            <input
              ref={fileInput}
              type="file"
              accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              onChange={(event) => setWorkbook(event.target.files?.[0] ?? null)}
              disabled={busy}
              required
            />
          </label>
          <button
            type="submit"
            className="primary-action"
            disabled={busy || !workbook}
          >
            {busy ? "Working…" : "Import workbook without notifications"}
          </button>
        </form>
        {importReport && (
          <dl className="detail-fields import-report">
            {[
              ["Source rows", "sourceRows"],
              ["Accepted rows", "acceptedRows"],
              ["Mapped rows", "mappedRows"],
              ["Unmapped rows", "unmappedRows"],
              ["Created records", "createdRecords"],
              ["Existing records", "existingRecords"],
              ["Quarantined rows", "quarantinedRows"],
              ["Notification events", "notificationEventsCreated"],
            ].map(([label, key]) => (
              <div className="detail-field" key={key}>
                <dt>{label}</dt>
                <dd>{importReport[key] ?? "Not reported"}</dd>
              </div>
            ))}
            <dt>Previously imported batch</dt>
            <dd>
              {importReport.reusedBatch ? "Yes — no duplicate import" : "No"}
            </dd>
          </dl>
        )}
      </details>
      <details
        className="candidate-queue"
        open={!selected && Object.keys(values).length === 0}
      >
        <summary>Review incoming reports</summary>
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
        {!candidates.length ? (
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
      </details>
      <button
        type="button"
        className="secondary-action"
        disabled={busy}
        onClick={() => choose(null)}
      >
        Add a new incident
      </button>
      {(selected || Object.keys(values).length > 0) && (
        <form className="incident-form" onSubmit={publish}>
          <h2>
            {readOnly
              ? `${selected.status === "rejected" ? "Rejected" : "Approved"} report`
              : selected
                ? "Verify and publish report"
                : "New incident"}
          </h2>
          {readOnly && (
            <p className="admin-help">
              This report is read-only. Completed reviews cannot be published
              again from this queue.
            </p>
          )}
          {selected?.status === "approved" && (
            <p className="admin-help">
              The fields below show the original source proposal.{" "}
              {selected.incidentId && (
                <a
                  href={`/?incident=${encodeURIComponent(selected.incidentId)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  View the published record with the reviewer’s changes.
                </a>
              )}
            </p>
          )}
          {selected?.status === "rejected" && (
            <section className="review-feedback">
              <h3>Saved rejection feedback</h3>
              <p>
                <strong>
                  {rejectionLabels[selected.rejectionReason] ||
                    selected.rejectionReason ||
                    "Reason not recorded"}
                </strong>
              </p>
              <p>{selected.rejectionNotes || "No additional explanation."}</p>
            </section>
          )}
          {selected && <EvidenceDetails candidate={selected} />}
          {selected && <CandidateRelationship candidate={selected} />}
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
          {!readOnly && (
            <>
              <button
                className="primary-action"
                type="submit"
                disabled={busy || showRejectForm || relationshipBlocked}
              >
                {busy ? "Publishing…" : "Publish verified incident"}
              </button>
              {relationshipBlocked && (
                <p className="admin-help">
                  Publishing as a new incident is blocked because this report
                  may relate to an existing incident. You can inspect its
                  sources or reject it if appropriate.
                </p>
              )}
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
                          <option value="duplicate">Duplicate incident</option>
                          <option value="insufficient_evidence">
                            Insufficient evidence
                          </option>
                          <option value="incorrect_information">
                            Incorrect information
                          </option>
                          <option value="other">Other</option>
                        </select>
                      </label>

                      <label>
                        Additional explanation
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
                        disabled={busy || !rejectionReason}
                        onClick={rejectCandidate}
                      >
                        {busy ? "Rejecting..." : "Confirm rejection"}
                      </button>
                    </div>
                  )}
                </>
              )}
            </>
          )}
          <button
            type="button"
            className="secondary-action"
            disabled={busy}
            onClick={() => {
              choose(null);
              setValues({});
            }}
          >
            Close report
          </button>
        </form>
      )}
    </div>
  );
}
