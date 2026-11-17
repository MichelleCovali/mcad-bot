import { useEffect, useMemo, useState } from "react";
import { createWorkspaceClient } from "../data/incidents.js";
import { AdminPanel } from "./AdminPanel.jsx";
import { AIMonitorSettings } from "./AIMonitorSettings.jsx";
import { TelegramSettings } from "./TelegramSettings.jsx";
import { LearningPanel } from "./LearningPanel.jsx";
import "./admin-workspace.css";

const sections = [
  ["overview", "Overview", "Track the archive, incoming reports and monitoring at a glance."],
  ["review", "Review queue", "Check sources, resolve uncertainties and publish verified incidents."],
  ["monitor", "Discovery", "Manage search coverage, run limits, usage and alerts."],
  ["learning", "Feedback & prompts", "Review Telegram feedback and evaluate changes before they go live."],
  ["team", "Team access", "Invite reviewers and manage workspace permissions."],
];

const formatDate = (value) => value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—";
const explorerUrl = import.meta.env.VITE_PUBLIC_ORIGIN || "/";

function Brand() {
  return <div className="workspace-brand">
    <img src="/assets/img/logo.png" alt="" />
    <span><strong>MCAD</strong><small>Operations workspace</small></span>
  </div>;
}

function SignIn({ client, inviteToken, loading, onSignedIn, sessionError, retry }) {
  const [email, emailChange] = useState("");
  const [password, passwordChange] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await (inviteToken ? client.acceptInvite({ token: inviteToken, password }) : client.login({ email, password }));
      passwordChange("");
      onSignedIn(result.user);
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }
  return <main className="workspace-auth">
    <div className="workspace-auth-intro">
      <Brand />
      <div>
        <p className="workspace-eyebrow">MARITIME CYBER ATTACK DATABASE</p>
        <h1>Clarity on the<br />maritime picture.</h1>
        <p>Review reports, manage discovery and keep the incident archive grounded in evidence.</p>
      </div>
      <a className="workspace-back" href={explorerUrl}>← Incident explorer</a>
    </div>
    <div className="workspace-auth-side">
      <div className="workspace-card workspace-signin">
        <p className="workspace-eyebrow">{inviteToken ? "TEAM INVITATION" : "PRIVATE WORKSPACE"}</p>
        <h2>{inviteToken ? "Join the MCAD team" : "Welcome back"}</h2>
        <p className="workspace-muted">{inviteToken ? "Set a password for the account in your invitation." : "Sign in with your MCAD account."}</p>
        {loading ? <p role="status">Checking your session…</p> : <>
          <form className="workspace-stack" onSubmit={submit}>
            {!inviteToken && <label>Email<input autoComplete="username" type="email" maxLength={254} required value={email} onChange={(e) => emailChange(e.target.value)} /></label>}
            <label>Password<input type="password" autoComplete={inviteToken ? "new-password" : "current-password"} minLength={inviteToken ? 12 : undefined} maxLength={256} required value={password} onChange={(e) => passwordChange(e.target.value)} /></label>
            {inviteToken && <small className="workspace-muted">Use at least 12 characters.</small>}
            <button type="submit" className="primary-action" disabled={busy}>{busy ? "Please wait…" : inviteToken ? "Accept invitation" : "Sign in"}</button>
          </form>
          {(error || sessionError) && <p className="inline-error" role="alert">{error || sessionError}</p>}
          {sessionError && <button className="secondary-action" type="button" onClick={retry}>Retry connection</button>}
          {inviteToken ? <p><a href="/admin">Back to sign in</a></p> : <p className="workspace-muted workspace-signin-foot">New here? Ask your MCAD administrator for an invitation.</p>}
        </>}
      </div>
    </div>
  </main>;
}

function Team({ client, user }) {
  const [team, setTeam] = useState(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("reviewer");
  const [link, setLink] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    client.team({ signal: controller.signal }).then(setTeam).catch((failure) => {
      if (!controller.signal.aborted) setError(failure.message);
    });
    return () => controller.abort();
  }, [client]);
  async function act(operation) {
    if (busy) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await operation();
      setTeam(await client.team());
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }
  function invite(event) {
    event.preventDefault();
    setLink("");
    return act(async () => {
      const result = await client.invite({ email, role });
      setLink(new URL("/admin#invite=" + encodeURIComponent(result.token), window.location.origin).href);
      setEmail("");
      setMessage("Invitation created. Copy the private link and share it with the intended recipient.");
    });
  }
  return <div className="workspace-stack">
    {error && <p className="inline-error" role="alert">{error}</p>}
    {message && <p className="success-message" role="status">{message}</p>}
    <section className="workspace-card">
      <h2>Invite a team member</h2>
      <p className="workspace-muted">Reviewers can verify and publish queued reports. Administrators can also manage the team and workspace settings.</p>
      <form className="workspace-inline-form" onSubmit={invite}>
        <label>Email address<input type="email" required maxLength={254} placeholder="name@example.com" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label>Role<select value={role} onChange={(e) => setRole(e.target.value)}><option value="reviewer">Reviewer</option><option value="admin">Administrator</option></select></label>
        <button type="submit" className="primary-action" disabled={busy}>Create invitation</button>
      </form>
      {link && <div className="workspace-invite">
        <label>One-time invitation URL<input readOnly value={link} onFocus={(e) => e.target.select()} /></label>
        <button type="button" className="secondary-action" onClick={async () => {
          try { await navigator.clipboard.writeText(link); setMessage("Invitation link copied."); }
          catch { setMessage("Select the invitation URL and copy it manually."); }
        }}>Copy link</button>
      </div>}
    </section>
    <section className="workspace-card">
      <h2>Members</h2>
      {!team ? <p>{error ? "Member list unavailable." : "Loading team…"}</p> : <ul className="workspace-rows">
        {team.members.map((member) => <li key={member.id}>
          <div><strong>{member.email}</strong><small>{member.id === user.id ? "You · " : ""}{member.active ? "Active" : "Disabled"}</small></div>
          <div className="workspace-row-actions">
            <label className="visually-hidden" htmlFor={"role-" + member.id}>Role for {member.email}</label>
            <select id={"role-" + member.id} disabled={busy || member.id === user.id} value={member.role} onChange={(e) => {
              const nextRole = e.target.value;
              act(() => client.updateMember(member.id, { role: nextRole }));
            }}><option value="admin">Administrator</option><option value="reviewer">Reviewer</option></select>
            {member.id !== user.id && <button type="button" className="secondary-action" disabled={busy} onClick={() => act(() => client.updateMember(member.id, { active: !member.active }))}>{member.active ? "Disable" : "Enable"}</button>}
          </div>
        </li>)}
      </ul>}
    </section>
    <section className="workspace-card">
      <h2>Invitations</h2>
      {!team?.invites.length ? <p className="workspace-muted">No invitations to show.</p> : <ul className="workspace-rows">
        {team.invites.map((item) => <li key={item.id}>
          <div><strong>{item.email}</strong><small>{item.role} · {item.status} · Expires {formatDate(item.expiresAt)}</small></div>
          {(item.status === "pending" || item.status === "active") && <button className="secondary-action" type="button" disabled={busy} onClick={() => act(async () => { await client.revokeInvite(item.id); setLink(""); })}>Revoke</button>}
        </li>)}
      </ul>}
    </section>
  </div>;
}

function Overview({ client, admin, readSection }) {
  const [health, setHealth] = useState(null);
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    function report(failure) { if (!controller.signal.aborted) setError(failure.message); }
    client.health({ signal: controller.signal }).then(setHealth).catch(report);
    if (admin) client.aiStatus({ signal: controller.signal }).then(setStatus).catch(report);
    return () => controller.abort();
  }, [client, admin]);
  return <div className="workspace-stack">
    {error && <p className="inline-error" role="alert">{error}</p>}
    <div className="workspace-stats">
      {[
        ["Published incidents", health?.incidents.total, "Records in the public archive"],
        ["Mapped records", health?.incidents.mapped, "With an attributed location"],
        ["Awaiting review", health?.feed.pendingCandidates, "Incoming reports to check"],
        ...(admin ? [["AI discovery", status ? status.effectiveEnabled ? "Active" : "Paused" : null, status?.effectiveEnabled ? "Scheduled searches enabled" : "No search is scheduled"]] : []),
      ].map(([label, value, note]) => <div className="workspace-card workspace-stat" key={label}><p>{label}</p><strong>{value ?? "—"}</strong><small>{note}</small></div>)}
    </div>
    <section className="workspace-card workspace-next">
      <div><p className="workspace-eyebrow">EDITORIAL DESK</p><h2>Reports need a human decision.</h2><p className="workspace-muted">Check the evidence, correct the record and publish once the facts are verified.</p></div>
      <button className="primary-action" type="button" onClick={() => readSection("review")}>Open review queue</button>
    </section>
  </div>;
}

function WorkbookImport({ client }) {
  const [file, setFile] = useState(null);
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  return <details className="workspace-card workspace-disclosure">
    <summary>Historical workbook import</summary>
    <p className="workspace-muted">Import an .xlsx file up to 10 MiB. Original fields are kept; historical imports do not send notifications.</p>
    <form className="workspace-inline-form" onSubmit={async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      setBusy(true);
      setStatus(null);
      try {
        const result = await client.importWorkbook(file);
        setStatus({ message: result.reusedBatch ? "This workbook was already imported." : `Import complete: ${result.createdRecords} added, ${result.quarantinedRows} held for review.` });
        setFile(null);
        form.reset();
      } catch (failure) { setStatus({ error: failure.message }); }
      finally { setBusy(false); }
    }}>
      <label>Excel workbook<input type="file" required accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(event) => setFile(event.target.files[0])} /></label>
      <button type="submit" className="secondary-action" disabled={busy || !file}>{busy ? "Importing…" : "Import historical data"}</button>
    </form>
    {status && <p className={status.error ? "inline-error" : "success-message"} role={status.error ? "alert" : "status"}>{status.error || status.message}</p>}
  </details>;
}

export function AdminWorkspace() {
  const [inviteToken, setInviteToken] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get("invite"));
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [section, setSection] = useState(() => new URLSearchParams(window.location.search).has("candidate") ? "review" : "overview");
  const [signingOut, setSigningOut] = useState(false);
  const client = useMemo(() => createWorkspaceClient({ onSessionExpired: () => {
    setUser(null);
    setError("Your session expired. Please sign in again.");
  } }), []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    client.session({ signal: controller.signal }).then((result) => {
      if (!controller.signal.aborted) setUser(result.user);
    }).catch((failure) => {
      if (!controller.signal.aborted) setError(failure.message);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [client, attempt]);
  function signedIn(account) {
    if (inviteToken) window.history.replaceState(null, "", window.location.pathname + window.location.search);
    setInviteToken(null);
    setUser(account);
    setError("");
  }
  if (loading || !user || inviteToken) return <SignIn client={client} inviteToken={inviteToken} loading={loading} sessionError={error} retry={() => setAttempt(attempt + 1)} onSignedIn={signedIn} />;
  const admin = user.role === "admin";
  const available = admin ? sections : sections.slice(0, 2);
  const selected = available.find(([key]) => key === section) ?? available[0];
  return <div className="workspace">
    <aside className="workspace-sidebar">
      <Brand />
      <nav aria-label="Administration">{available.map(([key, label], index) => <button type="button" key={key} aria-current={key === selected[0] ? "page" : undefined} onClick={() => setSection(key)}>
        <span aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>{label}
      </button>)}</nav>
      <div className="workspace-profile">
        <small>{admin ? "Administrator" : "Reviewer"}</small><strong>{user.email}</strong>
        <button type="button" disabled={signingOut} onClick={async () => {
          setSigningOut(true);
          setError("");
          try { await client.logout(); setUser(null); }
          catch (failure) { setError(failure.message); }
          finally { setSigningOut(false); }
        }}>{signingOut ? "Signing out…" : "Sign out"}</button>
      </div>
    </aside>
    <main className="workspace-main">
      <div className="workspace-top"><span>MCAD / Operations</span><a href={explorerUrl}>Incident explorer ↗</a></div>
      <div className="workspace-header">
        <p className="workspace-eyebrow">MARITIME INTELLIGENCE</p>
        <h1>{selected[1]}</h1><p>{selected[2]}</p>
      </div>
      {error && <p className="inline-error" role="alert">{error}</p>}
      {selected[0] === "overview" && <Overview client={client} admin={admin} readSection={setSection} />}
      {selected[0] === "review" && <AdminPanel client={client} canCreate={admin} />}
      {selected[0] === "monitor" && <div className="workspace-stack"><AIMonitorSettings client={client} /><TelegramSettings client={client} /><WorkbookImport client={client} /></div>}
      {selected[0] === "learning" && <LearningPanel client={client} />}
      {selected[0] === "team" && <Team client={client} user={user} />}
    </main>
  </div>;
}
