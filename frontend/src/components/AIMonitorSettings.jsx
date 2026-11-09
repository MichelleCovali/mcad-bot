import { useEffect, useState } from "react";

function readableDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "Unknown date" : date.toLocaleString();
}

export function AIMonitorSettings({ client, onRunComplete }) {
  const [status, setStatus] = useState(null);
  const [draft, setDraft] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  function accept(result) {
    setStatus(result);
    setDraft({ enabled: Boolean(result.enabled), runsPerDay: result.runsPerDay,
      lookbackHours: result.lookbackHours, maxCandidates: result.maxCandidates,
      maxToolCalls: result.maxToolCalls });
  }

  useEffect(() => {
    const controller = new AbortController();
    client.aiStatus({ signal: controller.signal }).then((result) => {
      if (!controller.signal.aborted) accept(result);
    }).catch((failure) => {
      if (!controller.signal.aborted) setError(failure.message);
    });
    return () => controller.abort();
  }, [client]);

  async function refresh() {
    setBusy(true);
    setError("");
    try { accept(await client.aiStatus()); }
    catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }

  async function save(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await client.saveAISettings({ ...draft, enabled: status.serverEnabled && draft.enabled });
      accept(result);
      setMessage(result.effectiveEnabled ? "Monitor settings saved. Scheduled AI searches are enabled." : "Monitor settings saved. AI search remains paused.");
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }

  async function run() {
    if (!status?.effectiveEnabled || busy) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await client.runAI();
      const failed = ["failed", "interrupted"].includes(result.status);
      if (failed) setError(result.error || `The search run ${result.status}. Review the run history below.`);
      else setMessage(`Search run finished. ${result.candidatesCreated ?? 0} reports added for human review.`);
      try {
        accept(await client.aiStatus());
        await onRunComplete?.();
      } catch (failure) {
        setError(`${failed ? `The search run ${result.status}. ` : "The search run finished. "}The view could not refresh. ${failure.message}`);
      }
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }

  return (
    <details className="ai-monitor-settings">
      <summary>AI monitoring settings — {status?.effectiveEnabled ? "enabled" : "paused"}</summary>
      {error && <p className="inline-error" role="alert">{error}</p>}
      {message && <p className="success-message" role="status">{message}</p>}
      {!status ? (
        <div>
          <p>{error ? "AI settings are unavailable. Report review remains available." : "Loading saved monitor settings…"}</p>
          {error && <button className="secondary-action" type="button" onClick={refresh} disabled={busy}>Retry settings</button>}
        </div>
      ) : (
        <>
          <p className="monitor-state" role="status">
            {status.effectiveEnabled ? "AI monitoring is enabled." : "AI search is paused. No AI search is scheduled."}
          </p>
          {!status.serverEnabled && <p className="admin-help">OpenAI search is disabled on the server. You can prepare the schedule, but enabling or running a search is locked.</p>}
          <p className="admin-help">OpenAI API key: {status.keyConfigured ? "configured on the server" : "not configured"}. Discovery: {status.discoveryModel}. Verification: {status.verificationModel}.</p>
          <form onSubmit={save}>
            <label className="monitor-toggle">
              <input type="checkbox" checked={draft.enabled} disabled={busy || !status.serverEnabled || (!status.keyConfigured && !draft.enabled)}
                onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} />
              Enable scheduled AI searches
            </label>
            <div className="incident-form-grid">
              <label>Runs per day
                <select value={draft.runsPerDay} disabled={busy} onChange={(event) => setDraft({ ...draft, runsPerDay: Number(event.target.value) })}>
                  {[3, 4, 6].map((count) => <option key={count} value={count}>{count} runs per day</option>)}
                </select>
              </label>
              <label>Look back (hours)
                <input type="number" min="1" max="168" step="1" required value={draft.lookbackHours} disabled={busy}
                  onChange={(event) => setDraft({ ...draft, lookbackHours: Number(event.target.value) })} />
              </label>
              <label>Maximum candidates per run
                <input type="number" min="1" max="10" step="1" required value={draft.maxCandidates} disabled={busy}
                  onChange={(event) => setDraft({ ...draft, maxCandidates: Number(event.target.value) })} />
              </label>
              <label>Web tool calls per run
                <input type="number" min="2" max="30" step="1" required value={draft.maxToolCalls} disabled={busy}
                  onChange={(event) => setDraft({ ...draft, maxToolCalls: Number(event.target.value) })} />
              </label>
            </div>
            <p className="admin-help">The agent searches the open web and compares original sources. Results enter the review queue; publication and Telegram notifications require approval. Token and tool limits constrain usage, but are not a guaranteed dollar cap.</p>
            <div className="admin-actions">
              <button className="primary-action" type="submit" disabled={busy}>{busy ? "Saving…" : "Save monitor settings"}</button>
              <button className="secondary-action" type="button" disabled={busy || !status.effectiveEnabled} onClick={run}>Run AI search now</button>
              <button className="secondary-action" type="button" disabled={busy} onClick={refresh}>Refresh status</button>
            </div>
          </form>
          <h3>Recent runs</h3>
          {!status.runs?.length ? <p>No AI searches have been run.</p> : <ul className="monitor-runs">
            {status.runs.map((entry) => <li key={entry.id}>
              <strong>{entry.status}</strong> · {readableDate(entry.startedAt)}
              <p>{entry.candidatesCreated ?? 0} reports added for review · {entry.webSearchCalls ?? 0} web tool calls · {entry.inputTokens ?? 0} input / {entry.outputTokens ?? 0} output tokens</p>
              {entry.error && <p className="inline-error">{entry.error}</p>}
            </li>)}
          </ul>}
        </>
      )}
    </details>
  );
}
