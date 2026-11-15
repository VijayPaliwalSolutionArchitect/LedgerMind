# Codebase Organization

*Version: v0.1 · 15-11-26 · Author: SHIVAM ITCS (JARVIS)*

A file-by-file map of the LedgerMind repository (`v1.0.0`). Every line of
behavior described here is verified against the actual source on `2026-11-13`.

```
LedgerMind/
├── README.md
├── .gitignore
├── backend/
│   ├── requirements.txt
│   ├── app/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── models.py
│   │   ├── schemas.py
│   │   ├── services.py
│   │   ├── seed.py
│   │   ├── main.py
│   │   └── routers/
│   │       ├── __init__.py
│   │       ├── deps.py
│   │       ├── accounting.py
│   │       ├── documents.py
│   │       ├── reconciliation.py
│   │       └── audit.py
│   ├── tests/
│   │   ├── conftest.py
│   │   └── test_ledgermind.py
│   └── data/                # runtime SQLite DB (gitignored)
└── docs/
    ├── ARCHITECTURE.md
    ├── TECH-STACK.md
    ├── FEATURES.md
    ├── RUNNING.md
    ├── DEPLOYMENT.md
    └── CODEBASE-ORGANIZATION.md
```

---

## `backend/app/` — the Python package

### `__init__.py`
Package marker; defines `__version__ = "1.0.0"` (surfaced by `/` and `/health`).

### `config.py` — configuration
All runtime settings are read from environment variables at import time, with
sane defaults:

| Name | Default | Env override |
|---|---|---|
| `DB_PATH` | `backend/data/ledgermind.db` | `LEDGERMIND_DB_PATH` |
| `RECONCILIATION_WINDOW_DAYS` | `3` | `LEDGERMIND_RECON_WINDOW` |
| `CONFIDENCE_THRESHOLD` | `"0.80"` | `LEDGERMIND_CONFIDENCE_THRESHOLD` |
| `DEFAULT_USER` | `"system"` | `LEDGERMIND_DEFAULT_USER` |

`.gitignore` excludes real DB files, so the repo never ships a database.

### `db.py` — database bootstrap & primitives
- `connect(db_path=None)` — opens a `sqlite3` connection with `Row` factory,
  `foreign_keys=ON`, WAL mode, and `check_same_thread=False`.
- `now_iso()` — isoformat timestamp used as the canonical `created_at`/
  `posted_at`/audit timestamp.
- `init_schema(conn)` — DDL for all **nine** tables:
  `accounts`, `journal_entries`, `journal_lines`, `categorization_rules`,
  `vendors`, `documents`, `statements`, `audit_log` (plus `sqlite_sequence`).
- `audit(conn, actor, action, entity, entity_id, detail)` — **append-only**
  insert into `audit_log`.
- `seed(conn)` — **idempotent** (row-count guarded) seed: 17 accounts, 27
  categorization rules, 2 vendors, 3 posted journal entries (6 lines). Money is
  inserted as TEXT.
- `ensure_schema_and_seed(db_path=None)` — creates the schema and runs `seed`
  if empty; returns a connection. Called by the app's lifespan hook and by
  `seed.py`.
- `connect_app()` — `ensure_schema_and_seed(config.DB_PATH)`.
- `fetch_id_by(conn, table, **criteria)` — id lookup by unique column(s)
  (e.g. account by code).
- `rows_to_dicts(rows)` — converts `sqlite3.Row` iterables to plain dicts.

### `models.py` — data-access layer
Thin, explicit SQL functions (each takes a `sqlite3.Connection`, returns plain
dicts / lists of dicts). Groups:

- **Accounts** — `list_accounts`, `create_account`, `get_account`,
  `balance_of`, `ledger`.
- **Journal entries** — `next_entry_no`, `create_journal_entry` (writes header
  + balanced lines; also inserts audit rows), `post_journal_entry`,
  `get_journal_entry`, `list_journal_entries`.
- **Categorisation rules** — `list_rules`, `create_rule` (raises `ValueError`
  on an unknown account), `match_rule` (uppercase `contains`, lowest priority
  wins, fallback to Sales Revenue `4000`).
- **Vendors** — `list_vendors`, `find_vendor`, `create_vendor`
  (insert-if-missing is driven from `services.ingest_document`).
- **Documents** — `save_document`, `get_document`, `list_documents`.
- **Reconciliation** — `store_statement`, `list_statements`,
  `reconcile_statement` (matches posted lines by amount/date within the
  configured window; memo-similarity tie-break via `_memo_score`).
- **Audit** — `list_audit` (optional entity filter).

### `schemas.py` — Pydantic v2 API contracts
- Types/enums: `AccountType` (`asset/liability/equity/income/expense`),
  `Side` (`debit/credit`), `EntryStatus` (`draft/posted`),
  `DocumentStatus` (`queued/posted/exception`),
  `DocumentType` (`invoice/receipt/po`), `MatchMode`.
- Request models: `AccountCreate`, `LineItem`, `JournalEntryCreate`,
  `PostJournalRequest`, `DocumentIngest`, `CategorizationRuleCreate`,
  `StatementImport`/`StatementLine`.
- Response models: `Account`, `JournalEntry`, `Document`, `DocumentResult`,
  `MatchResult`, `CategorizationRule`, `AuditRecord`, `AuditContext`.
- **Money is `Decimal` here** — the only place it exists as a number; it is
  serialised as TEXT-compatible strings.
- **Validation before SQL:** `JournalEntryCreate` enforces positive amounts
  and `sum(debits) == sum(credits)` via a model validator (so unbalanced
  journals get `422` before any row is written); `DocumentIngest` validates
  memo/amount/date.
- `AuditContext` records the acting user; `AuditRecord` models `audit_log`
  rows.

### `services.py` — domain services (the automation core)
`ingest_document(conn, ...)` runs the back-office pipeline end-to-end:

1. `_extract_account` — memo → rules → account (falls back to Sales Revenue).
2. `ensure_vendor` for the vendor master.
3. `_score_confidence` — deterministic weights (rule +0.55, vendor +0.25,
   non-trivial memo +0.10, positive amount +0.10; max 1.00).
4. `_validate` — non-empty memo, positive amount, not future-dated.
5. **Gate:** if valid **and** confidence ≥ `config.CONFIDENCE_THRESHOLD`, the
   document is **auto-posted** as a balanced journal entry (matched account
   against Bank `1100`) with `status="posted"` and the resulting
   `post_entry_id`; otherwise it is saved `status="exception"` with
   `reasons`.
6. Every stage and the final outcome are written to the audit log (including
   `action="auto_post"`).

The module docstring captures the design contract: *no auto-post without a
confidence gate + validation pass; failures land in the exception queue;
target straight-through rate > 80%*.

### `seed.py` — seed/reset CLI
Standalone entrypoint (`if __name__ == "__main__"`) for `python -m app.seed`:

- `seed_database(db_path)` — `ensure_schema_and_seed` + prints a summary.
- `reset_database(db_path)` — deletes the DB file, then re-seeds fresh.
- `main()` — argparse with `--reset` and `--path`.
- Guaranteed idempotent (existing rows are never duplicated).

### `main.py` — FastAPI entrypoint
- Builds `app = FastAPI(title="LedgerMind API", version=__version__)`.
- **Lifespan hook** creates `DB_PATH.parent`, runs
  `db.ensure_schema_and_seed`, logs the boot summary.
- Meta routes: `/` (service info), `/health`.
- Mounts the four domain routers via `include_router`.
- `if __name__ == "__main__": uvicorn.run("app.main:app", ...)` for
  `python -m app.main`.

### `routers/` — thin HTTP layer (no SQL, no rules)
- `__init__.py` — package marker + docstring.
- `deps.py` — DI: `get_db` (request-scoped connection, commit-on-success,
  always-close) and `get_audit` (reads `X-User`, defaults to `DEFAULT_USER` via
  `AuditContext(actor=...)`).
- `accounting.py` — `GET/POST /accounts`, `GET /accounts/{id}` (with balance),
  `GET /accounts/{id}/ledger`, `GET/POST /journal-entries`,
  `GET /journal-entries/{id}`, `POST /journal-entries/{id}/post`,
  `GET /system/config`. Duplicate account codes → `409`.
- `documents.py` — `POST /documents/ingest`, `GET /documents` (status filter),
  `GET /documents/{id}`, `GET/POST /categorization-rules` (unknown account →
  `400`), `GET /vendors`.
- `reconciliation.py` — `POST /statements/import`, `GET /statements`,
  `POST /statements/{id}/reconcile` (missing statement → `404`).
- `audit.py` — `GET /audit` (entity filter).

---

## `backend/tests/`

### `conftest.py`
Bootstrap that adds `backend/` to `sys.path` so `from app import ...` resolves
regardless of the current directory.

### `test_ledgermind.py`
Integration tests via `fastapi.testclient.TestClient`, pointed at a throwaway
SQLite file (`LEDGERMIND_DB_PATH` set to a temp path **before** the app is
imported). Covers:

- `test_health`, `test_seed_populated` (17 accounts / 2 vendors / 27 rules /
  3 entries).
- Accounts: list, create + zero balance, duplicate `409`, balanced entry,
  unbalanced entry `422`.
- Documents: high-confidence **auto-post** (`status=posted`, `confidence≥0.80`,
  `5500`, non-null `entry_id`), low-confidence **exception** (null `entry_id`,
  non-empty `reasons`), status filtering, rule CRUD + unknown-account `400`.
- Reconciliation: import two lines, reconcile → line 0 matched (Bank `1100`
  against the seeded rent entry), line 1 unmatched.
- Audit: pipeline events present, and `X-User` header stamped on created
  records.

**Status:** `14 passed`.

---

## `docs/`

- `INSTALLATION.md` — bare-metal → running API in < 10 min.
- `ARCHITECTURE.md` — layer diagram, module duties, money-as-text, document
  pipeline, data model, seed summary, reconciliation.
- `API.md` — full REST endpoint reference (methods, bodies, responses, errors).
- `TECH-STACK.md` — components + exact versions + env config.
- `FEATURES.md` — feature walkthrough with example API calls.
- `RUNNING.md` — install / run / test / seed / config + verification output.
- `DEPLOYMENT.md` — production deployment, backups, systemd/Docker, security.
- `env-config.md` — every environment variable + the `X-User` header.
- `PLAYBOOK-CROSSWALK.md` — Hero Projects Playbook (Project 10) → implementation map.
- `CODEBASE-ORGANIZATION.md` — this file.

## Layout invariants

- **Two dependency manifests exist** — `requirements.txt` at the repo root
  and `backend/requirements.txt`; the effective one for the backend is
  `backend/requirements.txt`. Both list the same five packages.
- **No frontend, no build step, no external services** — everything needed to
  run is inside `backend/` plus the five pip deps.
- **The database is a runtime artifact** — `.gitignore` excludes `*.db`
  (and the `backend/data/` directory), so a clone boots its own fresh,
  seeded DB on first start.