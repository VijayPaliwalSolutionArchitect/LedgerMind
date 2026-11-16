# LedgerMind — Future Expansion & Roadmap

*Version: v0.1 · 15-11-26 · Author: SHIVAM ITCS (JARVIS)*

Concrete expansion plan for LedgerMind (Hero Projects Playbook **Project 10 —
Document & Back-Office Automation Line**). This repo is the **process + output
layer** (ingest → extract → validate → confidence gate → auto-post → audit);
the crosswalk in [docs/PLAYBOOK-CROSSWALK.md](PLAYBOOK-CROSSWALK.md) marks
ingestion, approval bots, ERP connectors and idempotency as ⏳. Each item below
names the exact file/endpoint it extends (`services.py`, `routers/`, `models.py`,
`schemas.py`).

## 1. Features (close the declared gaps — correctness first)

| # | Expansion | Grounded in | Horizon |
|---|---|---|---|
| 1.1 | **Idempotency keys on ingest** — unique constraint + `409` on duplicate `documents.reference`; the playbook smoke test's "one duplicate is rejected" step | crosswalk §5/§6: "no idempotency key yet — gap"; NFR "duplicate posting impossible" | **Now** |
| 1.2 | **Dry-run / shadow mode** — post to a shadow ledger for the first weeks, compare vs human postings, report drift | crosswalk §3 NFR: "dry-run/shadow posting ⏳" | Next |
| 1.3 | **Exception review UI** — queue page showing extracted fields, match status, `reasons[]`, one-click resolve→post; target < 1 min review | FR-L6 ⚠️ data ✅ UI ⏳ (`GET /api/v1/documents?status=exception`) | Next |
| 1.4 | **Document-type packs as config** — new doc type in < 1 day without a schema change; today `doc_type` is a hard enum (`invoice|receipt|po`) | crosswalk deliverable 7 ⚠️ | Next |
| 1.5 | **Monthly accuracy / straight-through / exceptions report** — scheduled render from document status counts + audit | FR-L9 ⚠️ partial | Next |
| 1.6 | **Multi-currency + FX** — `Decimal` money-as-text already; add currency field, FX rate table, cross-currency reconciliation | `schemas.py` Decimal money; bank rec in FEATURES §4 | Later |
| 1.7 | **LLM extraction tier** — optional LiteLLM extraction for unstructured docs above the deterministic rules, with per-field confidence (deterministic stays the default/offline path) | crosswalk: extraction "deterministic rule-based" today; playbook wants LLM + confidence | Later |

## 2. Integrations

| # | Integration | Grounded in | Horizon |
|---|---|---|---|
| 2.1 | **OCR ingestion** — Stirling-PDF normalize/OCR + n8n inbox watch feeding `POST /api/v1/documents/ingest` (memo text stands in for OCR today) | FR-L1/L2; crosswalk §1 ingestion layer ⏳ | Next |
| 2.2 | **ERP / Tally connector** — push posted entries to external ERP/Tally sandbox; built-in SQLite GL remains the default | deliverable 3 ⚠️ "posts to the built-in GL" | Next |
| 2.3 | **Connector registration via NexusBridge** — register the ERP/Tally/HRMS connector + field mapping once instead of bespoke code | sibling product (connector-first integration workspace) | Later |
| 2.4 | **Approval bots** — Teams/WhatsApp one-tap approve/reject on exceptions with doc context; pair with **WapiDesk** for the WhatsApp leg | FR-L7 ⏳; `X-User` header already models approver identity | Next |
| 2.5 | **FortressStack gateway** — route the optional LLM extraction tier through the governed, egress-proof gateway with per-client budgets | sibling product | Later |
| 2.6 | **Langfuse + PulsePipe** — trace extraction/posting calls; stream audit events for portfolio observability | crosswalk §1 output layer lists Langfuse ⏳ | Later |

## 3. Scaling & platform

- **SQLite WAL → Postgres + worker queue** — ingestion becomes async (queue → worker → gate → post); the confidence gate in `services.ingest_document` is unchanged.
- **Multi-entity ledgers** — one deploy, many client books (chart of accounts is per-DB today; `db.seed()` 17 accounts is the template).
- **WORM/audit storage** — audit_log already insert-only; add hash-chaining + off-site export for audit-grade immutability.

## 4. Monetization

- **AP automation pricing** — per-document processed or per-seat; the straight-through rate (`LEDGERMIND_CONFIDENCE_THRESHOLD` dial) is the value metric on the invoice.
- **Exception-review as a service** — sell the review UI + bots for firms that keep their own ERP.
- **Compliance bundle** — audit reconstructability (100% of posted docs via `posted_entry_id` + `auto_post` rows) + idempotency proof as the procurement artifact for finance buyers.

## 5. Adjacent modules

- **LedgerMind + NexusBridge** — any ERP/CRM as the posting target via registered connectors.
- **LedgerMind + WapiDesk/HelpPilot** — vendor invoice questions from support tickets answered against the GL; approval bots on WhatsApp.
- **LedgerMind + FortressStack** — private document AI: OCR + extraction with zero external data flow.

---

*Source of truth for current behaviour: [docs/FEATURES.md](FEATURES.md),
[docs/API.md](API.md), [docs/PLAYBOOK-CROSSWALK.md](PLAYBOOK-CROSSWALK.md).*
