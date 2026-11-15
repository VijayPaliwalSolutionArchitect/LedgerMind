# Running LedgerMind

*Version: v0.1 · 15-11-26 · Author: SHIVAM ITCS (JARVIS)*

Everything below assumes **Python 3.10+** (developed against 3.14). All
commands run from the `backend/` directory.

> First-time setup from a bare machine: [`INSTALLATION.md`](INSTALLATION.md).
> Every environment variable: [`env-config.md`](env-config.md). Endpoint
> reference: [`API.md`](API.md).

## 1. Create and activate the virtual environment

Windows (git-bash / cmd):

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```

macOS / Linux:

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## 2. (Optional) Seed / reset the database

The app bootstraps and seeds its own database on first startup, so this step
is only needed for one-off CLI control or a reset.

```bash
# from backend/
.venv/Scripts/python -m app.seed                # create + seed (idempotent)
.venv/Scripts/python -m app.seed --reset        # wipe + reseed fresh
.venv/Scripts/python -m app.seed --path x.db    # seed a specific file
```

## 3. Run the server

```bash
.venv/Scripts/python -m uvicorn app.main:app --reload
# or, equivalently
.venv/Scripts/python -m app.main
```

- Interactive API docs: **http://localhost:8000/docs**
- ReDoc: **http://localhost:8000/redoc**
- OpenAPI schema: **http://localhost:8000/openapi.json**
- Health probe: **http://localhost:8000/health**

The SQLite database is created at `backend/data/ledgermind.db` on first boot.

### Smoke test (`curl`)

```bash
curl -s localhost:8000/health                          # {"status":"ok","version":"1.0.0"}
curl -s localhost:8000/api/v1/accounts | head -c 400
curl -s -X POST localhost:8000/api/v1/documents/ingest \
  -H 'content-type: application/json' \
  -d '{"doc_type":"invoice","memo":"rent for january","amount":"2500","entry_date":"2026-11-13","vendor_name":"Acme Supplies"}' \
  | jq '{status, confidence, matched_account_code, entry_id}'
```

## 4. Run the tests

```bash
cd backend
.venv/Scripts/python -m pytest -q
```

The suite (`tests/test_ledgermind.py`) runs against a **throwaway SQLite
database** (`LEDGERMIND_DB_PATH` is pointed at a temp file before the app is
imported), so your real data is never touched. It exercises:

- boot/health and idempotent seeding;
- account create / duplicate-reject / balance / ledger;
- balanced journal entries and rejection of **unbalanced** ones (`422`);
- the document pipeline: auto-post on high confidence, exception-queue on a
  no-match, status filtering, rule CRUD (incl. unknown-account `400`);
- statement import + reconciliation (a matched and an unmatched line);
- audit-trail presence and `X-User` actor stamping.

**Current status:** `14 passed` (see below for verification).

## 5. Configuration

All configuration is environment-variable driven; see
[`env-config.md`](env-config.md) for the full reference. For example:

```bash
LEDGERMIND_DB_PATH=/var/lib/ledgermind/app.db \
LEDGERMIND_CONFIDENCE_THRESHOLD=0.90 \
LEDGERMIND_RECON_WINDOW=5 \
.venv/Scripts/python -m uvicorn app.main:app --reload
```

## Verification (last confirmed run)

```text
$ .venv/Scripts/python -m pytest -q
..............                                            [100%]
14 passed, 1 warning in 0.94s

$ .venv/Scripts/python -m app.seed
[seed] database ready     -> C:\...\lm_seed_check.db
[seed] accounts               17
[seed] categorization_rules   27
[seed] vendors                2
[seed] journal_entries        3
[seed] journal_lines          6
[seed] documents              0
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: app` when running `pytest` from outside `backend/` | Run pytest from `backend/`, or rely on `tests/conftest.py` which adds `backend/` to `sys.path` |
| `AttributeError`/old API | Ensure only Pydantic **v2** is installed; the code uses v2-only idioms |
| `TestClient` StarletteDeprecationWarning | Harmless upstream `httpx` notice; tests still pass |
| Wrong database targeted | Unset `LEDGERMIND_DB_PATH` (or don't leave a stale value in your shell) |