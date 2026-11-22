# MCAD development

- Before implementation, read the [constitution](docs/constitution.md) and the relevant feature specification. Confirm which proposed requirements have been agreed. Use the [project overview](docs/project-overview.md) to find the current implementation and test setup.
- Inspect existing code, migrations, tests, and local changes before proposing replacements. Follow the agreed plan, preserve unrelated behavior and edits, and report conflicts between the specification and implementation.
- Keep decisions and task progress current in the feature documents. When a requirement changes, revise the specification and bring the plan and tasks into line before implementing it.
- Record required verification with each task and mark it complete only after that verification passes. Use the [isolated test setup](docs/ci.md).
- Work in small, scoped commits on the existing feature branch. Follow any additional instructions in the directory being changed.
