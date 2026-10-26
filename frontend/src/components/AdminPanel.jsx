import { useRef, useState } from "react";
import { createAdminClient, safeReferenceUrl } from "../data/incidents.js";

const fields = [
  ["title", "Title", true],
  ["description", "Summary", false, "textarea"],
  ["year", "Year", false, "number"],
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
];
const classification = [
  ["victimIdentity", "Victim identity"],
  ["affectedSystem", "Affected system"],
  ["method", "Method"],
  ["socialEngineeringTechnique", "Social engineering technique"],
  ["malwareType", "Malware type"],
  ["ransomwareType", "Ransomware type"],
  ["attackPattern", "Attack pattern"],
  ["motive", "Motive"],
  ["campaign", "Campaign"],
  ["impact", "Impact"],
  ["countermeasures", "Countermeasures"],
  ["threatActor", "Threat actor"],
  ["additionalInfo", "Additional information"],
];
export function AdminPanel({ onChanged }) {
  const tokenInput = useRef(null);
  const fileInput = useRef(null);
  const [token, setToken] = useState("");
  const [client, setClient] = useState(null);
  const [candidates, setCandidates] = useState([]);
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
  async function login(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const connection = createAdminClient(token);
      const records = await connection.candidates();
      setCandidates(records);
      setClient(connection);
      setToken("");
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
    setValues(
      candidate
        ? {
            title: candidate.title,
            description: candidate.summary,
            sourceUrl: candidate.sourceUrl,
            locationPrecision: "unknown",
          }
        : { locationPrecision: "unknown" },
    );
    setMessage("");
    setError("");
  }
  async function publish(event) {
    event.preventDefault();
    setError("");
    setMessage("");
    const payload = Object.fromEntries(
      Object.entries(values).filter(([, value]) => String(value).trim() !== ""),
    );
    if (!payload.title?.trim())
      return setError("Enter a verified incident title.");
    for (const field of ["year", "latitude", "longitude"])
      if (payload[field] != null) payload[field] = Number(payload[field]);
    const hasLat = payload.latitude !== undefined,
      hasLng = payload.longitude !== undefined;
    if (hasLat !== hasLng)
      return setError(
        "Supply both latitude and longitude, or leave both blank.",
      );
    if (
      (hasLat &&
        (!Number.isFinite(payload.latitude) ||
          Math.abs(payload.latitude) > 90)) ||
      (hasLng &&
        (!Number.isFinite(payload.longitude) ||
          Math.abs(payload.longitude) > 180))
    )
      return setError("Enter valid latitude and longitude.");
    if (payload.sourceUrl && !safeReferenceUrl(payload.sourceUrl))
      return setError("Use an HTTP or HTTPS reference URL.");
    payload.latitude ??= null;
    payload.longitude ??= null;
    if (!hasLat) payload.locationPrecision = "unknown";
    else if (
      !payload.locationPrecision ||
      payload.locationPrecision === "unknown"
    )
      return setError(
        "Specify the location accuracy of the supplied coordinates.",
      );
    setBusy(true);
    try {
      const result = selected
        ? await client.approve(selected.id, payload)
        : await client.create(payload);
      setMessage(
        hasLat
          ? "Incident saved. The map is updating."
          : "Incident published without a map point. Add verified coordinates before it can appear on the map.",
      );
      setSelected(null);
      setValues({});
      setCandidates(await client.candidates());
      onChanged(result);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }

  
  async function rejectCandidate() {
    if (!selected || !rejectionReason || busy) return;

    setBusy(true);
    setError("");
    setMessage("");

    try {
      await client.reject(selected.id, {
        reason: rejectionReason,
        notes: rejectionNotes,
      });

      setCandidates(await client.candidates());

      setSelected(null);
      setValues({});
      setShowRejectForm(false);
      setRejectionReason("");
      setRejectionNotes("");

      setMessage("Incident rejected. Feedback saved successfully.");
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
      onChanged(report);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }
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
        <p>{candidates.length} reports awaiting review</p>
        <button
          type="button"
          onClick={() => {
            setClient(null);
            setToken("");
            setValues({});
            setSelected(null);
            setWorkbook(null);
            setImportReport(null);
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
        {!candidates.length ? (
          <p>No reports awaiting review.</p>
        ) : (
          <ul>
            {candidates.map((candidate) => (
              <li key={candidate.id}>
                <strong>{candidate.title}</strong>
                <p>{candidate.summary}</p>
                {safeReferenceUrl(candidate.sourceUrl) && (
                  <a
                    href={safeReferenceUrl(candidate.sourceUrl)}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Read source
                  </a>
                )}
                <button type="button" onClick={() => choose(candidate)}>
                  Review
                </button>
              </li>
            ))}
          </ul>
        )}
      </details>
      <button
        type="button"
        className="secondary-action"
        onClick={() => choose(null)}
      >
        Add a new incident
      </button>
      {(selected || Object.keys(values).length > 0) && (
        <form className="incident-form" onSubmit={publish}>
          <h2>{selected ? "Verify and publish report" : "New incident"}</h2>
          <p>
            Publish confirmed facts only. Do not guess a location from the
            victim’s home country.
          </p>
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
                    step={type === "number" ? "any" : undefined}
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
                  <textarea
                    rows={2}
                    value={values[key] ?? ""}
                    onChange={(event) =>
                      setValues({ ...values, [key]: event.target.value })
                    }
                  />
                </label>
              ))}
            </div>
          </details>
          <button className="primary-action" type="submit" disabled={busy}>
            {busy ? "Publishing…" : "Publish verified incident"}
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

        </form>
      )}
    </div>
  );
}
