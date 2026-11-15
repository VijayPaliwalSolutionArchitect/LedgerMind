# LedgerMind — Project 10 Playbook Crosswalk (Acceptance Coverage Map)

*Version: v0.1 · 15-11-26 · Author: SHIVAM ITCS (JARVIS)*

Maps the **SHIVAM ITCS Hero Projects Playbook — Project Ten (LedgerMind:
Document & Back-Office Automation Line, playbook pp. 64–72)** to what this
repository actually implements, with exact file/endpoint references.

> **Scope note (important):** this codebase implements the playbook's
> **Process + Output layers** as a self-contained FastAPI service: schema-
> validated ingestion, deterministic extraction/confidence scoring,
> business-rule validation, master-data matching, the confidence gate,
> straight-through auto-posting into a real double-entry ledger, bank
> reconciliation, and an append-only audit trail. The **Ingestion layer**
> (n8n inbox watch, Stirling-PDF OCR) and the **approval bots / ERP-Tally
> connectors** are deliberately out of scope here — documents enter via
> `POST /api/v1/documents/ingest` (memo text stands in for OCR output), and
> posting lands in the built-in SQLite GL instead of an external ERP.

---

## 1. Playbook §10.2 reference architecture — layer mapping

Playbook Table 85 (ingestion / extraction / process / output):

| Playbook layer | Playbook components | In this repo? | Where |
|---|---|---|---|
| Ingestion | n8n inbox watch, Stirling-PDF normalize/OCR | ⚠️ API entry point only | `POST /api/v1/documents/ingest` (`routers/documents.py`) — memo text replaces OCR output |
| Extraction | LLM field extraction + Pydantic schemas + confidence | ✅ Deterministic rule-based extraction + confidence | `services.py::ingest_document`, `models.match_rule`, `schemas.py` (Decimal money) |
| Process | approval bots, exception queue UI, vendor-master matching | ✅ exception queue + vendor master; bots ⏳ | `documents.status="exception"` + `reasons[]`; `models.find_vendor/create_vendor` |
| Output | ERP/Tally push, Sheets sync, audit trail + Langfuse | ✅ built-in GL + audit trail | `models.create_journal_entry`, `audit_log`, `GET /api/v1/audit`; ERP connector / Langfuse ⏳ |

Playbook 6-step conveyor vs. repo:

| Playbook step | Status | Evidence |
|---|---|---|
| 1. n8n watches inbox; Stirling-PDF normalizes/OCRs | ⏳ out of scope | ingest endpoint is the queue entry |
| 2. Extraction service returns fields + confidence per doc type | ✅ (deterministic) | `services._score_confidence` (rule +0.55 / vendor +0.25 / memo +0.10 / amount +0.10) |
| 3. Pydantic validates structure; business rules validate sense | ✅ | `schemas.py` validators (balance, positive amounts); `services._validate` (memo, amount, future date) |
| 4. High-confidence auto-posts; rest queue as exceptions | ✅ | confidence gate vs `LEDGERMIND_CONFIDENCE_THRESHOLD` (default 0.80) → `posted` or `exception` |
| 5. Approvals via Teams/WhatsApp bots | ⏳ out of scope | audit actor via `X-User` header stands in for approver identity |
| 6. Every step lands in audit trail | ✅ | append-only `audit_log`, incl. `auto_post` events; `GET /api/v1/audit` |

---

## 2. Functional requirements (playbook Table 88) — FR-L mapping

| ID | Playbook requirement | Priority | Status in this repo | Evidence |
|----|----------------------|----------|--------------------|----------|
| FR-L1 | Watch inbox/hot folder, queue documents with status | Must | ⚠️ API queue entry | `POST /api/v1/documents/ingest`, `GET /api/v1/documents?status=` |
| FR-L2 | Normalise + OCR before extraction | Must | ⏳ out of scope (no Stirling-PDF) | memo text supplied directly |
| FR-L3 | Schema-validated field extraction with confidence per doc type | Must | ✅ deterministic | `doc_type` enum + rule matching + confidence score in `DocumentResult` |
| FR-L4 | Validate totals/tax; match vendors/POs against master data | Must | ✅ | `schemas.py` + `services._validate`; vendor master (`/api/v1/vendors`, auto-create) |
| FR-L5 | Auto-post above threshold, idempotently | Must | ✅ gate / ⚠️ idempotency | balanced auto-post vs Bank `1100`; **no duplicate-detection key yet** (see §5) |
| FR-L6 | Exception queue UI showing fields, image, match status | Must | ✅ data / ⏳ UI | `status="exception"` + machine-readable `reasons[]`; no document-image UI |
| FR-L7 | Approvals via Teams/WhatsApp one-tap bots | Should | ⏳ out of scope | — |
| FR-L8 | Audit trail export per document (end-to-end reconstructable) | Must | ✅ | `audit_log` append-only; `GET /api/v1/audit?entity=documents` |
| FR-L9 | Monthly accuracy / straight-through / exceptions report | Should | ⚠️ Partial | straight-through measurable via document status counts; no scheduled report yet |

## 3. Non-functional requirements (playbook §10.3)

| NFR | Target | Status in this repo |
|-----|--------|---------------------|
| No auto-post without threshold + validation pass | correctness culture | ✅ enforced in `services.ingest_document` (both gates required) |
| Dry-run / shadow posting for first weeks | — | ⏳ (no shadow-ledger mode yet) |
| Field accuracy > 95% on demo corpus | — | ⚠️ N/A for deterministic rules; LLM extraction would need eval harness |
| Straight-through > 80% | — | ✅ measurable: seeded rules + vendors auto-post typical invoices (`confidence 1.00`); tune via `LEDGERMIND_CONFIDENCE_THRESHOLD` |
| Audit: every document reconstructable end-to-end, immutable | 100% | ✅ append-only `audit_log` (insert-only; never updated/deleted) |
| Privacy: documents stay on the VPS; PII masking for external APIs | — | ✅ fully offline — no external model calls at all in this build |
| Availability 99.5% | — | single-process uvicorn + WAL SQLite; see [DEPLOYMENT.md](DEPLOYMENT.md) |
| Duplicate posting impossible (idempotency keys) | zero | ⏳ **gap** — no idempotency key on ingest yet; tracked below |

---

## 4. KPIs (playbook Table 89)

| Playbook KPI | Target | Measurable here? |
|---|---|---|
| Straight-through posting rate | > 80% | ✅ count `documents` by `status` (`posted` vs `exception`) |
| Field extraction accuracy | > 95% | ⚠️ deterministic matcher — accuracy is rule coverage, not a model metric |
| Exception review time | < 1 min | ⏳ needs the review UI |
| Duplicate postings | zero (idempotency) | ⏳ not yet enforced — see §5 |
| Audit reconstructability | 100% of posted docs | ✅ every posted doc has `posted_entry_id` + `auto_post` audit rows |

---

## 5. Deliverables & acceptance checks (playbook Table 91)

| # | Playbook deliverable | Acceptance check | Status in this repo |
|---|----------------------|------------------|---------------------|
| 1 | Live pipeline on demo corpus | 80%+ straight-through | ✅ via API: high-confidence docs (rule + known vendor) post at `confidence 1.00`; tune threshold |
| 2 | Exception queue UI | sub-minute review on camera | ⚠️ queue data + reasons ✅ (`GET /api/v1/documents?status=exception`); UI ⏳ |
| 3 | ERP/Tally sandbox posting, idempotent | visible in sandbox ledger | ⚠️ posts to the built-in GL (same double-entry shape); idempotency ⏳ |
| 4 | Approval bot demo (Teams/WhatsApp) | one-tap with doc context | ⏳ out of scope |
| 5 | Audit trail export | one document reconstructed end-to-end | ✅ `GET /api/v1/audit` |
| 6 | Accuracy report | monthly-style report renders | ⏳ |
| 7 | Document-type pack template | new type in < 1 day | ⚠️ `doc_type` is an enum (`invoice|receipt|po`) — adding a type is a schema change today, not config |

## 6. Acceptance smoke test (playbook §10.7)

> E-mail five demo documents; two post straight through, two land as
> exceptions (reviewed in < 1 min each), one duplicate is rejected by
> idempotency; the audit trail reconstructs all five.

Repo-coverable portion today (verified by `backend/tests/test_ledgermind.py`,
14 tests):

1. Ingest documents → `POST /api/v1/documents/ingest` (×5) ✅
2. High-confidence ones post straight through → `status:"posted"`, `entry_id` set, `auto_post` audit row ✅ (test asserts `5500` match, `confidence ≥ 0.80`)
3. Low-confidence ones land as exceptions with reasons → `status:"exception"`, `reasons[]` ✅
4. Audit reconstructs all five → `GET /api/v1/audit` ✅
5. Duplicate rejected by idempotency → ⏳ **not implemented** (no idempotency key on `documents.reference` yet)

## 7. Playbook §10.7 deployment steps vs. this repo

Steps 1–3 (compose: ledger-api, n8n, stirling-pdf, litellm, langfuse; demo
inbox; schema packs into PostgreSQL) are platform-level. This repo is the
**ledger-api** slice, self-contained on SQLite:

| Playbook step | This repo |
|---|---|
| Deploy ledger-api (+ Caddy site) | `uvicorn app.main:app` — see [DEPLOYMENT.md](DEPLOYMENT.md) |
| Load vendor master + seeds | ✅ `db.seed()` (17 accounts, 27 rules, 2 vendors, 3 entries) on first boot |
| Validation rules + matching; dry-run sandbox | ✅ Pydantic + `_validate` + vendor master; dry-run ⏳ |
| Exception UI + approval bots | ⚠️ exception data via API; UI/bots ⏳ |
| Audit storage, reporting, monitoring, backups | ✅ `audit_log` + WAL file backups in DEPLOYMENT.md; reporting/monitoring ⏳ |

## 8. Portfolio angle (playbook §10.8)

Keywords: "invoice automation AI", "accounts payable automation", "document
AI", "ERP integration agent". The money metric is the **straight-through
rate** — this repo's confidence gate (`LEDGERMIND_CONFIDENCE_THRESHOLD`) is
exactly the dial that trades auto-post volume against exception load.

---

## 9. Source references

| Playbook artefact | Where implemented / verified |
|---|---|
| Table 85 architecture layers | `docs/ARCHITECTURE.md` |
| Table 88 FR-L1…L9 | §2 above; `routers/`, `services.py`, `models.py`, `tests/test_ledgermind.py` |
| Table 89 KPIs | §4 above; `GET /api/v1/documents`, `GET /api/v1/audit` |
| Table 91 deliverables | §5 above |
| §10.7 smoke test | §6 above |

*The playbook source file is not checked into this repo; section/table numbers
refer to the SHIVAM ITCS Hero Projects Playbook, Project Ten chapter
(pp. 64–72).*
