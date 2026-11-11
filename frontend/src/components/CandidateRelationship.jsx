const relationshipLabels = {
  unclassified: "Not classified",
  new_incident: "Proposed new incident",
  possible_update: "Possible update",
  possible_duplicate: "Possible duplicate",
};

export function CandidateRelationship({ candidate }) {
  if (!candidate) return null;

  const type = candidate.relationshipType ?? "unclassified";
  const relatedId = candidate.relatedIncidentId;

  return (
    <section className="candidate-evidence">
      <h3>Relationship to existing incidents</h3>

      <p>
        <strong>
          {relationshipLabels[type] ?? "Relationship needs review"}
        </strong>
      </p>

      <p>
        {candidate.relationshipReason ||
          "No matching explanation has been recorded."}
      </p>

      {relatedId && (
        <p>
          <a
            href={`/?incident=${encodeURIComponent(relatedId)}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            Open related incident
          </a>
        </p>
      )}

      {(type === "possible_update" ||
        type === "possible_duplicate" ||
        relatedId) && (
        <p className="admin-help">
          This is a suggested connection, not a confirmed match. Review the
          existing incident and source evidence before deciding how to handle
          this report.
        </p>
      )}
    </section>
  );
}
