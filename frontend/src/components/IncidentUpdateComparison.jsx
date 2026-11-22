import { incidentUpdateOptions } from "../data/incidentUpdates.js";

export function IncidentUpdateComparison({
  incident,
  draft,
  selectedKeys,
  onSelectionChange,
  disabled = false,
}) {
  const options = incidentUpdateOptions(incident, draft);

  function toggle(key, checked) {
    onSelectionChange(
      checked
        ? [...new Set([...selectedKeys, key])]
        : selectedKeys.filter((selected) => selected !== key),
    );
  }

  return (
    <fieldset className="review-fields" disabled={disabled}>
      <legend>Choose reviewed changes</legend>

      <p className="admin-help">
        Select only information you have checked against the sources. Unselected
        fields and existing references will be preserved.
      </p>

      {options.length === 0 ? (
        <p>
          No different, nonblank proposals are available for the supported text
          fields.
        </p>
      ) : (
        options.map((option) => (
          <section className="candidate-evidence" key={option.key}>
            <label className="review-check">
              <input
                type="checkbox"
                checked={selectedKeys.includes(option.key)}
                onChange={(event) => toggle(option.key, event.target.checked)}
              />{" "}
              Update {option.label}
            </label>

            <div className="review-comparison">
              <div><strong>Current value</strong><p>{option.current || "Not recorded"}</p></div>
              <div><strong>Proposed value</strong><p>{option.proposed}</p></div>
            </div>
          </section>
        ))
      )}
    </fieldset>
  );
}
