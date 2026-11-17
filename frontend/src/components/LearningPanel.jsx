import { useEffect, useState } from "react";
import { safeReferenceUrl } from "../data/incidents.js";

const reasons = { relevant: "Relevant", duplicate: "Duplicate", old_news: "Old news",
  not_maritime: "Not maritime-related", incorrect_information: "Incorrect information", insufficient_evidence: "Insufficient evidence" };
const blank = { summary: "", discoveryInstructions: "", verificationInstructions: "" };
const date = (value) => value ? new Date(value).toLocaleDateString() : "—";
const label = (value) => reasons[value] || (value ?? "").replaceAll("_", " ");

function FeedbackCard({ item, busy, onReview }) {
  const [notes, setNotes] = useState("");
  const source = safeReferenceUrl(item.sourceUrl);
  return <li>
    <div><strong>{item.title}</strong><small>{item.editorialDecision ? "Editorial rejection · " : ""}{label(item.reason)} · {date(item.createdAt)}</small></div>
    {source && <a href={source} target="_blank" rel="noopener noreferrer">Open report source ↗</a>}
    {item.comment && <p>{item.comment}</p>}
    {item.reviewNotes && <p className="workspace-muted">Reviewer notes: {item.reviewNotes}</p>}
    {item.status === "pending" && <form className="workspace-stack" onSubmit={(e) => {
      e.preventDefault();
      if (notes.trim()) onReview(item.id, { status: "confirmed", notes: notes.trim() });
    }}>
      <label>Verified facts for the regression case<textarea maxLength={2000} rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="For example: the report republishes the same incident from July 2024." /></label>
      <div className="admin-actions">
        <button className="primary-action" disabled={busy || !notes.trim()}>Confirm as test case</button>
        <button className="secondary-action" type="button" disabled={busy} onClick={() => onReview(item.id, { status: "dismissed", notes: notes.trim() })}>Dismiss</button>
      </div>
    </form>}
  </li>;
}

function VersionResults({ client, id }) {
  const [view, setView] = useState(null);
  const [error, setError] = useState("");
  return <div className="workspace-stack">
    <button className="secondary-action" type="button" onClick={async () => {
      setError("");
      try { setView((await client.versionResults(id)).items); }
      catch (failure) { setError(failure.message); }
    }}>View case results</button>
    {error && <p className="inline-error" role="alert">{error}</p>}
    {view && (view.length ? <ul className="workspace-rows workspace-feedback">
      {view.map((entry) => <li key={entry.caseId + ":" + entry.model}><strong>{entry.title}</strong><small>{entry.passed ? "Passed" : "Failed"} · {entry.expected === "other" ? "Rejection expected" : `${label(entry.expected)} expected`} · {label(entry.actual)} returned · {entry.model}</small><p>{entry.rationale}</p></li>)}
    </ul> : <p className="workspace-muted">No results yet.</p>)}
  </div>;
}
export function LearningPanel({ client }) {
  const [data, setData] = useState(null);
  const [draft, setDraft] = useState(blank);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    client.learning({ signal: controller.signal }).then(setData).catch((failure) => {
      if (!controller.signal.aborted) setError(failure.message);
    });
    return () => controller.abort();
  }, [client]);
  async function act(work, success) {
    if (busy) return;
    setBusy(true); setError(""); setMessage("");
    try {
      await work();
      setMessage(success);
      try { setData(await client.learning()); }
      catch (failure) { setError("The change was saved, but this view could not refresh. " + failure.message); }
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }
  const active = data?.versions.find((item) => item.id === data.activeVersionId);
  return <div className="workspace-stack">
    {error && <p className="inline-error" role="alert">{error}</p>}
    {message && <p className="success-message" role="status">{message}</p>}
    {!data ? <p role="status">{error ? "Feedback is unavailable." : "Loading feedback and versions…"}</p> : <>
      <div className="workspace-stats">
        <div className="workspace-card workspace-stat"><p>Awaiting feedback review</p><strong>{data.pendingFeedbackCount}</strong><small>Earlier ratings that still need a decision</small></div>
        <div className="workspace-card workspace-stat"><p>Confirmed regression cases</p><strong>{data.regressionCaseCount}</strong><small>Verified facts to replay before release</small></div>
        <div className="workspace-card workspace-stat"><p>Prompt in use</p><strong>{active ? "Custom" : "Built-in"}</strong><small>{active?.summary || "Default MCAD instructions"}</small></div>
      </div>
      <section className="workspace-card">
        <h2>Editorial feedback</h2>
        <p className="workspace-muted">Rejections from the editorial Telegram chat and the review queue are saved with the editor’s explanation.</p>
        {!data.feedback.length ? <p>No editorial feedback yet.</p> : <ul className="workspace-rows workspace-feedback">
          {data.feedback.map((item) => <FeedbackCard key={item.id} item={item} busy={busy} onReview={(id, payload) => act(() => client.reviewFeedback(id, payload), payload.status === "confirmed" ? "Feedback confirmed and added to regression cases." : "Feedback dismissed.")} />)}
        </ul>}
      </section>
      <section className="workspace-card" id="prompt-draft">
        <h2>Draft a prompt update</h2>
        <p className="workspace-muted">When enabled, AI uses recent editorial rejections to draft changes to the search and verification prompts. Review and evaluate a draft before activating it.</p>
        {!data.aiEnabled && <p className="workspace-muted">AI proposal and evaluation are paused by the server. You can still review feedback and save a draft.</p>}
        <button type="button" className="secondary-action" disabled={busy || !data.aiEnabled || !data.regressionCaseCount} onClick={() => act(() => client.proposeVersion(), "AI suggestion saved as a draft for review.")}>Suggest an update with AI</button>
        <form className="workspace-stack" style={{ marginTop: "1rem" }} onSubmit={(e) => {
          e.preventDefault();
          act(async () => { await client.createVersion(draft); setDraft(blank); }, "Prompt draft saved.");
        }}>
          <label>What does this change fix?<input maxLength={500} required value={draft.summary} onChange={(e) => setDraft({ ...draft, summary: e.target.value })} /></label>
          <div className="incident-form-grid">
            <label>Discovery instructions<textarea rows={4} maxLength={1400} value={draft.discoveryInstructions} onChange={(e) => setDraft({ ...draft, discoveryInstructions: e.target.value })} /></label>
            <label>Verification instructions<textarea rows={4} maxLength={1400} value={draft.verificationInstructions} onChange={(e) => setDraft({ ...draft, verificationInstructions: e.target.value })} /></label>
          </div>
          <div><button className="primary-action" disabled={busy || !(draft.discoveryInstructions.trim() || draft.verificationInstructions.trim())}>Save new draft</button></div>
        </form>
      </section>
      <section className="workspace-card">
        <h2>Versions and evaluation</h2>
        <p className="workspace-muted">Replay confirmed cases against a draft. Passing the recorded cases is required for activation and does not guarantee future accuracy.</p>
        {active && <button type="button" className="secondary-action" disabled={busy} onClick={() => act(() => client.restoreVersion(null), "Built-in prompt restored.")}>Restore built-in prompt</button>}
        {!data.versions.length && <p>No custom versions yet.</p>}
        {data.versions.map((item) => {
          const isActive = item.id === data.activeVersionId;
          const ready = data.regressionCaseCount > 0 && item.pendingCases === 0 && item.failedCases === 0 && item.passedCases === data.regressionCaseCount;
          return <article className="workspace-version workspace-stack" key={item.id}>
            <div><strong>{item.summary}</strong><div className="workspace-muted">{isActive ? "Active" : item.lastActivatedAt ? "Previously active" : "Draft"} · {item.createdByAI ? "AI suggestion" : "Manual"} · {date(item.createdAt)} · {item.passedCases} passed / {item.failedCases} failed / {item.pendingCases} pending</div></div>
            <details><summary>Read instructions</summary>
              <h3>Discovery</h3><pre className="workspace-prompt">{item.discoveryInstructions || "No additions"}</pre>
              <h3>Verification</h3><pre className="workspace-prompt">{item.verificationInstructions || "No additions"}</pre>
              <VersionResults client={client} id={item.id} />
            </details>
            <div className="admin-actions">
              <button className="secondary-action" type="button" disabled={busy || !data.aiEnabled || !item.pendingCases} onClick={() => act(() => client.evaluateVersion(item.id), "Regression results recorded.")}>Evaluate next cases</button>
              <button className="secondary-action" type="button" disabled={busy} onClick={() => {
                setDraft({ summary: item.summary, discoveryInstructions: item.discoveryInstructions, verificationInstructions: item.verificationInstructions });
                document.querySelector("#prompt-draft input")?.focus();
              }}>Use as new draft</button>
              {!isActive && <button className="primary-action" type="button" disabled={busy || !ready} onClick={() => act(() => client.activateVersion(item.id), "Prompt version activated for future searches.")}>Activate version</button>}
              {!isActive && item.lastActivatedAt && <button className="secondary-action" type="button" disabled={busy} onClick={() => act(() => client.restoreVersion(item.id), "Previous prompt version restored.")}>Roll back to this version</button>}
            </div>
          </article>;
        })}
      </section>
    </>}
  </div>;
}
