import { useEffect, useRef, useState } from "react";
import { IncidentRevisionHistory } from "./IncidentRevisionHistory.jsx";
import { IncidentUpdateComparison } from "./IncidentUpdateComparison.jsx";
import {
  incidentUpdateSources,
  selectedIncidentChanges,
  UPDATE_TEXT_FIELDS,
} from "../data/incidentUpdates.js";

export function IncidentUpdateReview({
  client, candidate, draft, onDraftChange, reason, onReasonChange,
  canViewHistory, disabled, onApplied, onBusyChange, onReportReloaded,
}) {
  const [records, setRecords] = useState(null);
  const [listError, setListError] = useState("");
  const [listRetry, setListRetry] = useState(0);
  const [query, setQuery] = useState("");
  const [incidentId, setIncidentId] = useState("");
  const [incident, setIncident] = useState(null);
  const [selectedKeys, setSelectedKeys] = useState([]);
  const [selectedSourceUrls, setSelectedSourceUrls] = useState([]);
  const [independentSources, setIndependentSources] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [needsReload, setNeedsReload] = useState(false);
  const request = useRef(null);
  const errorNotice = useRef(null);
  const targetHeading = useRef(null);
  const searchInput = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    client.incidents({ signal: controller.signal })
      .then((items) => {
        if (!controller.signal.aborted) setRecords(items);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setListError(failure.message);
      });
    return () => controller.abort();
  }, [client, listRetry]);

  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => { if (error) errorNotice.current?.focus(); }, [error]);
  useEffect(() => { if (incident) targetHeading.current?.focus(); }, [incident]);

  const sourceOptions = incidentUpdateSources(draft, incident);
  const currentUrls = new Set(incidentUpdateSources({}, incident).map((source) => source.url));
  const selectedSources = sourceOptions.filter((source) => selectedSourceUrls.includes(source.url));
  const factualChange = selectedKeys.length > 0;
  const hasChange = selectedKeys.length > 0 || selectedSources.some((source) => !currentUrls.has(source.url));
  const search = query.trim().toLowerCase();
  const matches = records?.filter(({ title, year }) =>
    `${title} ${year ?? ""}`.toLowerCase().includes(search),
  );
  const targetOptions = matches && incidentId && !matches.some((record) => record.id === incidentId)
    ? [...matches, ...records.filter((record) => record.id === incidentId)]
    : matches;

  function clearComparison() {
    setIncident(null);
    setSelectedKeys([]);
    setSelectedSourceUrls([]);
    setConfirmed(false);
    setIndependentSources(false);
    setShowHistory(false);
    setError("");
  }

  async function loadTarget(id, reloadReport = false) {
    request.current?.abort();
    clearComparison();
    setIncidentId(id);
    setNeedsReload(reloadReport);
    if (!id) {
      setWorking(false);
      return;
    }

    const controller = new AbortController();
    request.current = controller;
    setWorking(true);
    try {
      if (reloadReport) {
        const report = await client.candidate(candidate.id, { signal: controller.signal });
        if (controller.signal.aborted) return;
        onReportReloaded(report);
        if (report.status !== "pending" || report.relationshipType === "new_incident") return;
      }
      const record = await client.incident(id, { signal: controller.signal });
      if (controller.signal.aborted) return;
      if (!record.reviewVersion) throw new Error("The incident has no review version.");
      setIncident(record);
      setNeedsReload(false);
    } catch (failure) {
      if (!controller.signal.aborted) {
        setError(failure.message);
        setNeedsReload(true);
      }
    } finally {
      if (!controller.signal.aborted) setWorking(false);
    }
  }

  async function applyUpdate(event) {
    event.preventDefault();
    if (disabled || working || needsReload || !incident || !confirmed ||
        !reason.trim() || !selectedSources.length || !hasChange || (factualChange && !independentSources)) return;
    const { id, reviewVersion } = incident;
    setError("");
    setWorking(true);
    onBusyChange(true);
    try {
      const changes = selectedIncidentChanges(incident, draft, selectedKeys);
      const result = await client.applyUpdate(candidate.id, {
        incidentId: id,
        reviewVersion,
        confirmation: { incidentId: id, reviewVersion },
        reason: reason.trim(),
        independentSourcesConfirmed: factualChange && independentSources,
        changes: { ...changes, sources: selectedSources },
      });
      await onApplied(result);
    } catch (failure) {
      if (["stale_target", "report_resolved", "report_changed"].includes(failure.code)) {
        setNeedsReload(true);
        setConfirmed(false);
      }
      setError(failure.message);
    } finally {
      setWorking(false);
      onBusyChange(false);
    }
  }

  return (
    <form className="incident-update-review" onSubmit={applyUpdate}>
      <p>Choose a published incident and compare the report with the existing record.</p>
      {(listError || error) && <p className="inline-error" role="alert" tabIndex={-1} ref={errorNotice}>{error || listError}</p>}
      {needsReload && (
        <button type="button" className="secondary-action" disabled={working}
          onClick={() => loadTarget(incidentId, true)}>Reload and compare</button>
      )}
      {listError && (
        <button type="button" onClick={() => {
          setListError("");
          setListRetry((count) => count + 1);
        }}>Retry incident list</button>
      )}
      {!records && !listError && <p role="status">Loading incident list…</p>}
      {records?.length === 0 && <p>No published incidents are available.</p>}
      {records?.length > 0 && (
        <fieldset className="review-fields" disabled={disabled || (needsReload && Boolean(incident))}>
          <legend>Existing incident</legend>
          <label>
            Search by title or year
            <input ref={searchInput} type="search" value={query} onChange={(event) => setQuery(event.target.value)} />
          </label>
          <label>
            Published target
            <select value={incidentId} onChange={(event) => loadTarget(event.target.value)}>
              <option value="">Choose an incident</option>
              {targetOptions.map((record) => (
                <option key={record.id} value={record.id}>
                  {record.title} · {record.year ?? "Year unknown"}{record.id === (candidate.relatedIncidentId ?? candidate.suggestedIncidentId) ? " · Suggested match" : ""}
                </option>
              ))}
            </select>
          </label>
          {matches.length === 0 && <p role="status">No published incidents match this search.</p>}
        </fieldset>
      )}
      {working && <p role="status">{disabled ? "Applying update…" : "Loading incident and review status…"}</p>}
      {incident && !needsReload && (
        <>
          <h4 ref={targetHeading} tabIndex={-1}>{incident.title}</h4>
          <div className="admin-actions">
            <button type="button" className="secondary-action" disabled={disabled} onClick={() => {
              loadTarget("");
              searchInput.current?.focus();
            }}>Change target</button>
            {canViewHistory && <button type="button" className="secondary-action" aria-expanded={showHistory}
              onClick={() => setShowHistory(!showHistory)}>
              {showHistory ? "Hide revision history" : "Revision history"}
            </button>}
          </div>
          {showHistory && <IncidentRevisionHistory key={incident.id} client={client} incidentId={incident.id} />}
          <details>
            <summary>Edit proposed text or add supporting links</summary>
            <div className="incident-form-grid">
              {UPDATE_TEXT_FIELDS.map(([key, label]) => (
                <label key={key}>{label}<textarea rows={2} value={draft[key] ?? ""} disabled={disabled}
                  onChange={(event) => {
                    setIndependentSources(false);
                    setSelectedKeys((keys) => keys.filter((field) => field !== key));
                    setConfirmed(false);
                    onDraftChange({ ...draft, [key]: event.target.value });
                  }} /></label>
              ))}
              <label className="wide-field">Supporting URLs (one per line)
                <textarea rows={2} disabled={disabled} value={draft.referenceUrlsText ?? ""} onChange={(event) => {
                  setIndependentSources(false);
                  setSelectedSourceUrls([]);
                  setConfirmed(false);
                  onDraftChange({ ...draft, referenceUrlsText: event.target.value });
                }} />
              </label>
            </div>
          </details>
          <IncidentUpdateComparison incident={incident} draft={draft} selectedKeys={selectedKeys}
            onSelectionChange={(keys) => { setSelectedKeys(keys); setIndependentSources(false); }} disabled={disabled} />
          <fieldset className="review-fields" disabled={disabled}>
            <legend>Sources supporting this update</legend>
            {!sourceOptions.length && <p>No valid source links are available. Add a supporting URL to continue.</p>}
            {sourceOptions.map((source) => (
              <div key={source.url} className="review-source">
                <label className="review-check">
                  <input type="checkbox" checked={selectedSourceUrls.includes(source.url)} onChange={(event) => {
                    setIndependentSources(false);
                    setSelectedSourceUrls(event.target.checked
                      ? [...selectedSourceUrls, source.url]
                      : selectedSourceUrls.filter((url) => url !== source.url));
                  }} />
                  Include {source.shortName || source.url} {currentUrls.has(source.url) ? "(already recorded)" : "(new source)"}
                </label>
                <a href={source.url} target="_blank" rel="noopener noreferrer">Open {source.shortName || source.url}</a>
                {source.rawText && <blockquote>{source.rawText}</blockquote>}
              </div>
            ))}
          </fieldset>
          <label>Reason for applying this update
            <textarea value={reason} onChange={(event) => onReasonChange(event.target.value)}
              maxLength={2000} rows={3} disabled={disabled} required />
          </label>
          {selectedSources.length > 0 && !hasChange && (
            <p role="status">Select a changed field or add useful new evidence to continue.</p>
          )}
          {factualChange && (
            <label className="review-check">
              <input type="checkbox" checked={independentSources} disabled={disabled}
                onChange={(event) => setIndependentSources(event.target.checked)} />
              I checked that two independent reporting origins support these changes
            </label>
          )}
          <label className="review-check">
            <input type="checkbox" checked={confirmed} disabled={disabled}
              onChange={(event) => setConfirmed(event.target.checked)} />
            Confirm {incident.title} as the target
          </label>
          <p>
            {selectedKeys.length} {selectedKeys.length === 1 ? "field" : "fields"} and {selectedSources.length} supporting {selectedSources.length === 1 ? "source" : "sources"} selected.
            The update preserves earlier evidence and records a revision.
          </p>
          <button type="submit" className="primary-action"
            disabled={disabled || working || !confirmed || !reason.trim() || !selectedSources.length || !hasChange || (factualChange && !independentSources)}>
            {working ? "Applying update…" : "Apply reviewed update"}
          </button>
        </>
      )}
    </form>
  );
}
