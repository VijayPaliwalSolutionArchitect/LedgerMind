# LedgerMind

*Version: v0.1 · 15-11-26 · Author: SHIVAM ITCS (JARVIS)*

**Document & back-office automation for double-entry bookkeeping.**

LedgerMind is a FastAPI + SQLite backend that turns messy business documents
(invoices, receipts, purchase orders) and bank statements into a clean,
auditable general ledger. An AI-flavoured **document pipeline** extracts a
memo, amount and vendor from each document, scores its confidence against a
set of categorisation rules, and either **auto-posts** a balanced journal
entry or routes the document to an **exception queue** for human review.

As of `v1.0.0` the repo contains a complete, runnable backend. There is no
frontend yet — the API and interactive docs are the product.

> This repository implements the **process + output layers** of the LedgerMind
> playbook (Hero Projects Playbook, **Project Ten**): schema-validated
> ingestion, deterministic extraction/confidence scoring, validation,
> master-data matching, the confidence gate, straight-through auto-posting
> into a double-entry ledger, bank reconciliation, and an append-only audit
> trail. Inbox watching/OCR (n8n + Stirling-PDF) and ERP/Tally connectors plug
> in at the edges — see [`docs/PLAYBOOK-CROSSWALK.md`](docs/PLAYBOOK-CROSSWALK.md).

---

## Quick start

Requires **Python 3.10+** (developed against 3.14). Full setup from a bare
machine: [`docs/INSTALLATION.md`](docs/INSTALLATION.md); run/test detail:
[`docs/RUNNING.md`](docs/RUNNING.md).

```bash
cd backend
python -m venv .venv                     # create the virtual environment
.venv/Scripts/python -m pip install -r requirements.txt   # Windows venv
# . .venv/bin/activate && pip install -r requirements.txt  # mac/Linux

.venv/Scripts/python -m app.seed         # create + seed the SQLite DB (idempotent)
.venv/Scripts/python -m uvicorn app.main:app --reload

# open http://localhost:8000/docs
```

The app bootstraps its own database (schema **and** seed data) on first
startup, so you can skip the `seed` step entirely.

**Run the test suite:** `cd backend && .venv/Scripts/python -m pytest -q` — all
tests pass against a throwaway database (see
[`docs/RUNNING.md`](docs/RUNNING.md#4-run-the-tests)).

---

## What it does

| Domain | API prefix | Capability |
|---|---|---|
| **General ledger** | `/api/v1/accounts`, `/api/v1/journal-entries` | Chart of accounts, balanced double-entry journal entries (auto-validate debits = credits), account balances & running ledger, draft → post workflow |
| **Document pipeline** | `/api/v1/documents/`, `/api/v1/categorization-rules`, `/api/v1/vendors` | Extract → validate → confidence gate → auto-post or exception queue; rule and vendor master CRUD |
| **Reconciliation** | `/api/v1/statements/` | Import bank statements (JSON lines), auto-match lines to posted ledger entries |
| **Audit** | `/api/v1/audit` | Append-only audit trail for end-to-end reconstructability |

Every money value is stored as **TEXT in SQLite** and converted to `Decimal`
only at the API boundary, so amounts round-trip exactly — no binary float
drift (see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#money-as-text)).

---

## Repository layout

```
LedgerMind/
├── README.md
├── .env.example                # every env var, documented (placeholders only; no secrets)
├── .gitignore
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI entrypoint (uvicorn app.main:app)
│   │   ├── config.py               # env-driven configuration
│   │   ├── db.py                   # SQLite bootstrap, seed, audit primitive
│   │   ├── models.py               # data-access layer (explicit SQL -> dicts)
│   │   ├── schemas.py              # Pydantic v2 API contracts (Decimal money)
│   │   ├── services.py             # document-pipeline business logic
│   │   ├── seed.py                 # `python -m app.seed` CLI (--reset, --path)
│   │   └── routers/                # thin HTTP layer per domain
│   │       ├── accounting.py
│   │       ├── documents.py
│   │       ├── reconciliation.py
│   │       ├── audit.py
│   │       └── deps.py
│   ├── tests/                      # integration tests (pytest + TestClient)
│   └── data/                       # SQLite DB lives here at runtime (gitignored)
└── docs/
    ├── INSTALLATION.md             # bare-metal -> running API in < 10 min
    ├── ARCHITECTURE.md             # design decisions, modules, data model
    ├── API.md                      # every endpoint: methods, bodies, responses, errors
    ├── TECH-STACK.md               # stack + versions + env config
    ├── FEATURES.md                 # feature walkthrough with example calls
    ├── RUNNING.md                  # install / run / test / seed-reset
    ├── DEPLOYMENT.md               # deployment, env config, production notes
    ├── env-config.md               # every environment variable / header
    ├── PLAYBOOK-CROSSWALK.md       # playbook Project 10 -> implementation map
    └── CODEBASE-ORGANIZATION.md    # file-by-file map
```

See [`docs/CODEBASE-ORGANIZATION.md`](docs/CODEBASE-ORGANIZATION.md) for a
module-by-module tour.

---

## Design highlights

- **Money as TEXT** — exact decimal round-trip end to end. All arithmetic
  happens in `Decimal` at the schema boundary.
- **Validation before SQL** — Pydantic enforces shape **and** sense (positive
  amounts, balanced journal entries) before any row is written.
- **Routers stay SQL-free** — the HTTP layer calls `app.models` /
  `app.services`, which own every query and return plain dicts.
- **Straight-through automation gate** — a document auto-posts only when it
  clears validation *and* its confidence score clears the configured threshold
  (default `0.80`); otherwise it lands in the exception queue with
  machine-readable reasons.
- **Immutable audit trail** — every mutation is appended, so any posted document
  can be reconstructed image → extraction → validation → posting.

---

## Documentation

- [`docs/INSTALLATION.md`](docs/INSTALLATION.md) — from bare metal to a running API in < 10 min.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — architecture, module duties, data model (all tables), the money-as-text decision.
- [`docs/API.md`](docs/API.md) — full REST reference: every endpoint, request/response schema, error codes.
- [`docs/TECH-STACK.md`](docs/TECH-STACK.md) — stack and exact versions.
- [`docs/FEATURES.md`](docs/FEATURES.md) — feature-by-feature walkthrough with example API calls.
- [`docs/RUNNING.md`](docs/RUNNING.md) — install, run, test, seed/reset.
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — deployment, env config, production notes.
- [`docs/env-config.md`](docs/env-config.md) — every environment variable + the `X-User` header.
- [`docs/PLAYBOOK-CROSSWALK.md`](docs/PLAYBOOK-CROSSWALK.md) — Hero Projects Playbook (Project 10) → implementation coverage map.
- [`docs/CODEBASE-ORGANIZATION.md`](docs/CODEBASE-ORGANIZATION.md) — file-by-file map.
- [`docs/FUTURE-EXPANSION.md`](docs/FUTURE-EXPANSION.md) — roadmap: features, integrations, scaling, monetization.