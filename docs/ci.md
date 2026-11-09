# Automated checks

The `MCAD CI` workflow runs on every push, pull request and manual dispatch. Its
four stable job names are `backend`, `postgres`, `frontend` and `container`.
GitHub displays each result in the pull request's Checks tab and the repository's
Actions tab. A red result includes the failing test or build log. Merging a pull
request also triggers the push checks on the resulting `main` commit.

- `backend` runs the Python suite with isolated SQLite databases and synthetic
  OpenAI/Telegram clients.
- `postgres` creates a disposable PostgreSQL 18 service, upgrades an existing v1
  database through all migrations, checks repeat migration and preserves existing
  data. It also exercises atomic candidate alerts and per-recipient notification
  suppression when a human publishes the candidate.
- `frontend` installs the lockfile, runs browser-data/form tests, builds the
  production frontend and checks the hosting output.
- `container` builds the production Docker image, starts the actual application
  as its non-root user, then checks readiness, frontend serving and administrator
  authentication. It uses a SQLite database under `/tmp`, with AI and external
  workers disabled.

Actions are pinned to official release commit SHAs. The workflow has read-only
repository permission and needs no repository secrets. It never receives the
Railway database URL, Telegram token, OpenAI key or administrator token.
`tests/conftest.py` clears inherited credentials before importing the application
and blocks external `requests` HTTP calls. Tests must use injected clients;
FastAPI's in-process test transport and local PostgreSQL remain available.

Install local test dependencies and run checks:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
cd frontend
npm ci
npm test
npm run build
npm run test:sites
```

PostgreSQL checks are opt-in locally. Set `MCAD_TEST_POSTGRES_URL` to a disposable
loopback PostgreSQL database whose name starts with `mcad_ci`. Each test creates
and removes its own randomly named schema. Remote databases and production-like
database names are refused. CI supplies this database automatically.

The private workbook is intentionally excluded from Git and CI. Its existing
opt-in checks run locally when `MCAD_TEST_WORKBOOK` points at the file. CI uses
synthetic data for ordinary importer and field-roundtrip checks.

The workflow reports failures; repository administrators can additionally make
these four checks required in a branch ruleset. CI itself neither runs AI
research nor deploys the application.
