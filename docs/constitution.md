# MCAD project constitution

These proposed principles are for team review. The checks describe how to verify them when a change is relevant.

| Principle | How to verify it |
| --- | --- |
| A reviewer approves publication and material updates to published incidents. | Test that ingestion produces a pending report and that the relevant public record changes only after the explicit, authenticated decision. |
| Keep source claims, corroborated information, and unknowns distinguishable. | Review a fixture with conflicting reports and partial dates. Check the saved record and public display for unsupported certainty or precision. |
| Preserve evidence, provenance, and revision history. | Update an existing fixture and check that earlier sources and original data survive and that the revision links the report, evidence, and before/after values. |
| AI activation is explicit and usage is controlled. | With fake clients, verify the activation gates, pause behavior, usage limits, and usage reporting. |
| Review actions are understandable and accessible. | Walk through each decision with a keyboard, then repeat at a narrow viewport and check that errors explain how to recover. |
| Test meaningful changes before committing. | Run the relevant [checks](ci.md) and record results and any gaps. |
| Preserve existing behavior unless an agreed specification intentionally changes it. | Compare affected entry points and older records against the baseline and verify documented compatibility. |
| Protect secrets and keep real client data separate from development fixtures. | Use synthetic fixtures and disposable databases, and inspect staged files and logs for credentials and client data. |
| Evolve the database with new versioned migrations. | Upgrade an older disposable database, check retained data, and rerun migrations to verify repeatability. |
| Work in small, reviewable commits on feature branches. | Check branch, staged diff, and verification results before each commit. |
