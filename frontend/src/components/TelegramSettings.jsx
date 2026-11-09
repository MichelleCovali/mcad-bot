import { useEffect, useState } from "react";

export function TelegramSettings({ client }) {
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    client.telegramStatus({ signal: controller.signal }).then(setStatus).catch((failure) => {
      if (!controller.signal.aborted) setError(failure.message);
    });
    return () => controller.abort();
  }, [client]);
  async function sendTest() {
    if (busy) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await client.testTelegram();
      setMessage("Test notification sent. No AI search or incident publication was started.");
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }
  return <details className="ai-monitor-settings">
    <summary>Telegram notifications</summary>
    <p>New source-checked AI reports are sent once for review. Uncertain reports stay in the website queue. Publishing a report does not repeat an alert already received.</p>
    {status && <p>{status.configured ? "Bot connected." : "Bot is not configured."} {status.activeSubscribers} active subscribers · {status.pendingDeliveries} pending deliveries.</p>}
    <p className="admin-help">Send one clearly labelled test to the configured test chat. This does not run AI or publish an incident.</p>
    {error && <p className="inline-error" role="alert">{error}</p>}
    {message && <p className="success-message" role="status">{message}</p>}
    <button type="button" className="secondary-action" onClick={sendTest} disabled={busy || !status?.configured || !status?.testChatConfigured}>
      {busy ? "Sending test…" : "Send Telegram test notification"}
    </button>
  </details>;
}
