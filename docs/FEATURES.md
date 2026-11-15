# Features

*Version: v0.1 · 15-11-26 · Author: SHIVAM ITCS (JARVIS)*

A walkthrough of every capability in LedgerMind `v1.0.0`, with example API
calls. All endpoints live under `/api/v1`; interactive docs are at `/docs`.
Full endpoint reference: [`API.md`](API.md).

**Conventions used below:** an optional `X-User` header stamps the acting
user into the audit trail (defaults to `system`). Money fields are JSON
strings that round-trip as exact `Decimal` values.

---

## 1. Chart of accounts

`GET /api/v1/accounts` — list the chart:

```bash
curl -s localhost:8000/api/v1/accounts | jq '.[] | [.code, .name, .type, .normal_balance]'
```

`POST /api/v1/accounts` — create an account (returns `201`; `409` on a
duplicate `code`):

```json
{ "code": "5700", "name": "Legal & Professional Fees", "type": "expense", "normal_balance": "debit" }
```

`GET /api/v1/accounts/{id}` — account plus its live balance
(`{"account": {...}, "balance": "0.00"}`).

`GET /api/v1/accounts/{id}/ledger` — every posted line touching the account.

The chart ships with 17 pre-seeded accounts spanning all five account types.

---

## 2. Journal entries (double-entry GL)

`POST /api/v1/journal-entries` — create a **balanced** entry. Validation (via
Pydantic, *before* any SQL) rejects unbalanced journals automatically
(`422`):

```json
{
  "memo": "Reimburse travel",
  "entry_date": "2026-11-13",
  "lines": [
    { "account_id": 15, "side": "debit",  "amount": "125.50" },
    { "account_id": 2,  "side": "credit", "amount": "125.50" }
  ]
}
```

(Account `15` is Travel & Meals `5400`; account `2` is Bank `1100`.)

- `status` defaults to `draft` (unposted) unless `"status": "posted"`.
- Negative/zero amounts and a reference to an unknown account are rejected.

`POST /api/v1/journal-entries/{id}/post` — promote a draft to posted
(`posted_at` is stamped; bodies may pass `{"actor": "..."}`).

`GET /api/v1/journal-entries` — list (filter by `status`, paginate with
`limit`/`offset`). Each entry embeds its `lines`, `total_debit`,
`total_credit` and `balance`.

No monetary value is ever stored as a float — debits always equal credits on a
posted entry.

---

## 3. Document & back-office automation pipeline

This is LedgerMind's headline feature: turn raw documents into posted ledger
entries *automatically*, and route the uncertain ones to humans.

### Ingest a document

`POST /api/v1/documents/ingest`

```json
{
  "doc_type": "invoice",
  "memo": "aws cloud hosting monthly service",
  "amount": "199.99",
  "entry_date": "2026-11-13",
  "vendor_name": "Acme Supplies",
  "reference": "INV-4471",
  "source": "inbox"
}
```

The pipeline: **extract → score confidence → validate → gate**.

- A high-memo match plus a known vendor produces **confidence `1.00`**;
  validation passes; so the document is **auto-posted** — a balanced journal
  entry is created (matched account vs. Bank `1100`) and the document is
  stored with `status: "posted"`, `confidence: "1.00"`, and its
  `posted_entry_id`.

```json
{
  "doc_type": "invoice", "memo": "aws cloud hosting monthly service",
  "amount": "199.99", "entry_date": "2026-11-13",
  "vendor_name": "Acme Supplies", "reference": "INV-4471",
  "status": "posted", "confidence": "1.00",
  "matched_account_code": "5500", "matched_account_name": "Software & Subscriptions",
  "entry_id": 4, "vendor": "Acme Supplies", "reasons": []
}
```

- A document with **no matching rule / unknown vendor / bad data** scores below
  the threshold and is routed to the **exception queue** instead —
  `status: "exception"`, `entry_id: null`, with human-readable `reasons`
  (e.g. `confidence 0.00 below threshold 0.80`).

### Inspect the queue

- `GET /api/v1/documents?status=posted|exception|queued` — list with a status
  filter.
- `GET /api/v1/documents/{id}` — a single document's full record.

### Confidence scoring (deterministic)

| Signal | Weight |
|---|---|
| Categorisation rule matched the memo | +0.55 |
| Vendor found in (or auto-created from) the master | +0.25 |
| Memo is non-trivial (≥4 chars) | +0.10 |
| Amount is positive | +0.10 |
| **Maximum** | **1.00** |

Auto-post happens only when `valid_and_confidence ≥ LEDGERMIND_CONFIDENCE_THRESHOLD`
(default `0.80`). A full rule match + known vendor (`1.00`) sails through; a
rule match alone (`0.85`) also clears the gate.

### Categorisation rules

`GET /api/v1/categorization-rules` and `POST /api/v1/categorization-rules`:

```json
{ "keyword": "office supplies", "account_code": "5000", "priority": 90 }
```

- Matches are case-insensitive `contains`; `priority` is used as a tie-breaker
  (lower number matched first).
- An `account_code` that does not exist → `400`.
- **27 rules ship pre-seeded**, e.g. `RENT→5200`, `SALARY/PAYROLL/WAGES→5100`,
  `AWS/AZURE/GCP/STRIPE/SUBSCRIPTION/SOFTWARE→5500`,
  `MARKETING/ADS/FACEBOOK/GOOGLE ADS→5600`, `AIRLINE/FLIGHT/HOTEL/TRAVEL→5400`,
  `ELECTRICITY/UTILITIES/WATER/GAS→5300`, `INVOICE/SALE→4000`,
  `INTEREST→4300`.
- An unknown keyword falls back to `SALES REVENUE (4000)` — see
  `models.match_rule`.

### Vendor master

`GET /api/v1/vendors` — list vendors (2 seeded: Acme Supplies, Vertex
Solutions). A document referencing an unknown vendor name **auto-creates** the
vendor (parity with the master is maintained) and continues through the
pipeline.

---

## 4. Bank-statement reconciliation

Import a statement and auto-match its lines to posted ledger entries.

`POST /api/v1/statements/import`

```json
{
  "source_name": "HDFC Bank Oct",
  "lines": [
    { "amount": "2500.00", "direction": "out", "memo": "office rent",   "posted_on": "2026-01-07" },
    { "amount": "90000.00", "direction": "out", "memo": "unknown transfer", "posted_on": "2026-01-08" }
  ]
}
```

`POST /api/v1/statements/{id}/reconcile` — returns one result per line. Lines
are matched against **posted** journal lines within
`RECONCILIATION_WINDOW_DAYS` (default 3) of the posted date, by amount and
date; a memo-similarity score breaks ties. A matched line reports
`matched: true` plus the matched `entry_id`/`entry_no`/`account_code` (the
seeded ₹2,500 rent entry is the natural demo target). Unmatched lines stay
marked `matched: false`.

`GET /api/v1/statements` — list imported statements.

---

## 5. Audit trail

`GET /api/v1/audit` — the append-only log. All mutations emit an entry:

```json
{ "actor": "controller@example.com", "action": "create",
  "entity": "journal_entries", "entity_id": 5, "detail": "memo=...", "created_at": "..." }
```

Filter with `?entity=accounts` etc. Every document autocreated by the pipeline
logs `action: "auto_post"` on `entity: "documents"`. Because the log is
append-only, any posted document can be reconstructed end-to-end.

---

## 6. Utility endpoints

- `GET /` — service name, version, docs link, health.
- `GET /health` — `{"status": "ok", "version": "1.0.0"}` (readiness probe).
- `GET /api/v1/system/config` — exposes the effective runtime configuration
  (DB path, confidence threshold, reconciliation window, default user).

## Seed / reset CLI

`python -m app.seed` (as a one-off, outside the server) prints DB contents;
`python -m app.seed --reset` wipes and reseeds. The running server does this
automatically on startup.