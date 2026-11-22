import { useEffect, useState } from "react";
import { safeReferenceUrl } from "../data/incidents.js";
import { UPDATE_TEXT_FIELDS } from "../data/incidentUpdates.js";

const labels = Object.fromEntries(UPDATE_TEXT_FIELDS);

function displayValue(value) {
  if (value === null || value === undefined || value === "") return "Not recorded";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

export function IncidentRevisionHistory({ client, incidentId }) {
  const [items, setItems] = useState(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    client.revisions(incidentId, { signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) setItems(result.items);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(failure.message);
      });
    return () => controller.abort();
  }, [client, incidentId, retry]);

  if (error) return (
    <>
      <p role="alert">{error}</p>
      <button type="button" onClick={() => {
        setError("");
        setRetry((count) => count + 1);
      }}>Retry revision history</button>
    </>
  );
  if (!items) return <p role="status">Loading revision history…</p>;
  if (!items.length) return <p>No revisions recorded.</p>;
  return (
    <ul className="revision-history">
      {items.map((revision) => (
        <li key={revision.id}>
          <details>
            <summary>
              <time dateTime={revision.createdAt}>{new Date(revision.createdAt).toLocaleString()}</time>
              {" · "}{revision.reason}
            </summary>
            {revision.changes.map(({ field, before, after }) => (
              <div key={field}>
                <h4>{labels[field] ?? field.replace(/([a-z])([A-Z])/g, "$1 $2")}</h4>
                <p>Previous value: {displayValue(before)}</p>
                <p>New value: {displayValue(after)}</p>
              </div>
            ))}
            {revision.addedSources.length > 0 && (
              <>
                <h4>Added sources</h4>
                <ul>
                  {revision.addedSources.map((url) => (
                    <li key={url}>{safeReferenceUrl(url)
                      ? <a href={url} target="_blank" rel="noopener noreferrer">{url}</a>
                      : url}</li>
                  ))}
                </ul>
              </>
            )}
          </details>
        </li>
      ))}
    </ul>
  );
}
