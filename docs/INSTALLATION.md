# LedgerMind — Installation Guide

*Version: v0.1 · 15-11-26 · Author: SHIVAM ITCS (JARVIS)*

This guide takes a brand-new machine from bare metal to a running LedgerMind
API in under 10 minutes. If a step fails, the cause is almost always the
environment, not the code.

---

## 1. Prerequisites

| Tool | Version required | Why |
|------|------------------|-----|
| Python | **3.10+** (developed and verified on 3.14) | `X | None` union syntax needs ≥3.10; Pydantic v2 idioms |
| Git | latest | Clone the repo |

**That is all.** No database server (SQLite is embedded), no Node, no Docker,
no external services — the document pipeline is deterministic and offline.

```bash
python --version    # expect 3.10 or newer
```

---

## 2. Get the code

```bash
git clone <repo-url> LedgerMind
cd LedgerMind
```

---

## 3. Python environment (isolated, never global)

```bash
cd backend
python -m venv .venv

# activate per shell:
#   Linux/macOS:            source .venv/bin/activate
#   Windows Git Bash/MSYS:  source .venv/Scripts/activate
#   Windows PowerShell:     .\.venv\Scripts\Activate.ps1

pip install --upgrade pip
pip install -r ../requirements.txt     # fastapi, uvicorn[standard], pydantic, pytest, httpx
```

(The effective manifest for the backend is the root `requirements.txt`; both
list the same five packages.)

---

## 4. Configuration (optional — everything has a default)

LedgerMind has four environment variables, all optional
(full reference: [`docs/env-config.md`](env-config.md); template:
`.env.example`). **The app reads them at import time and has no dotenv
loader**, so export them in your shell if you want non-defaults; otherwise
skip this step:

```bash
export LEDGERMIND_DB_PATH=backend/data/ledgermind.db   # the default anyway
export LEDGERMIND_CONFIDENCE_THRESHOLD=0.80            # auto-post gate
export LEDGERMIND_RECON_WINDOW=3                       # reconciliation window (days)
export LEDGERMIND_DEFAULT_USER=system                  # audit actor default
```

There are **no secrets** in this project — no API keys or tokens; the pipeline
is offline. `.env` is gitignored; only `.env.example` (placeholders) is
committed.

---

## 5. First run — the app boots and seeds itself

```bash
# from backend/
uvicorn app.main:app --reload
# or: python -m app.main
# or, using the venv python explicitly on Windows:
.venv/Scripts/python -m uvicorn app.main:app --reload
```

On startup the lifespan hook creates `backend/data/ledgermind.db`, applies
the schema and idempotently seeds: **17 accounts, 27 categorisation rules,
2 vendors, 3 posted journal entries (6 lines)**.

One-off CLI control (optional — the server does this automatically):

```bash
python -m app.seed              # create + seed (idempotent), print summary
python -m app.seed --reset      # wipe + reseed fresh
python -m app.seed --path x.db  # target a specific file
```

---

## 6. Verify

```bash
curl -s http://localhost:8000/health
# {"status":"ok","version":"1.0.0"}

curl -s http://localhost:8000/api/v1/accounts | head -c 400
# seeded chart of accounts

curl -s http://localhost:8000/api/v1/system/config
# {"db_path":..., "reconciliation_window_days":3, "confidence_threshold":"0.80", "default_user":"system"}
```

Open in a browser:

| Target | URL |
|--------|-----|
| Swagger UI | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |
| OpenAPI spec | http://localhost:8000/openapi.json |

Quick pipeline smoke test:

```bash
curl -s -X POST localhost:8000/api/v1/documents/ingest \
  -H 'content-type: application/json' \
  -H 'X-User: demo@example.com' \
  -d '{"doc_type":"invoice","memo":"aws cloud hosting monthly service","amount":"199.99",
       "entry_date":"2026-11-15","vendor_name":"Acme Supplies","reference":"INV-4471"}' \
  | jq '{status, confidence, matched_account_code, entry_id}'
# expect status=posted, confidence=1.00, matched_account_code=5500, entry_id=<n>
```

---

## 7. Run the tests

```bash
cd backend
python -m pytest -q        # 14 tests against a throwaway temp DB
```

The suite points `LEDGERMIND_DB_PATH` at a temp file **before** importing the
app, so your real database is never touched.

---

## Common gotchas

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ModuleNotFoundError: app` | running from the wrong directory | run from `backend/` (or rely on `tests/conftest.py`, which fixes `sys.path`) |
| `AttributeError`/Pydantic v1 errors | wrong Pydantic major version | install Pydantic **v2** (`pip install -U pydantic`) |
| `StarletteDeprecationWarning` in tests | harmless upstream httpx notice | ignore; tests still pass |
| Wrong database targeted | stale `LEDGERMIND_DB_PATH` in your shell | unset it, or check `GET /api/v1/system/config` |
| `database is locked` | many writers on one SQLite file | keep workers 1–4; WAL mode is already enabled per connection |
| Want a clean slate | old demo data | `python -m app.seed --reset` (or delete `backend/data/ledgermind.db` and restart) |
| `422` on a journal entry | unbalanced debits/credits or non-positive amount | the Pydantic validator rejects it before any SQL — fix the payload |

---

*Source of truth: `README.md` + `docs/ARCHITECTURE.md`. If this text ever
disagrees with the code, the code wins.*
