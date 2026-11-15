# LedgerMind — API Reference

*Version: v0.1 · 15-11-26 · Author: SHIVAM ITCS (JARVIS)*

Complete reference for the LedgerMind REST API, written from the actual
routers in `backend/app/routers/` and contracts in `backend/app/schemas.py`.

- **Base URL:** `http://localhost:8000`; all domain routes live under `/api/v1`
- **Auth:** none in this build. Send an optional **`X-User: <name>`** header —
  it is stamped as the `actor` into the append-only `audit_log` (defaults to
  `LEDGERMIND_DEFAULT_USER`, usually `system`).
- **Money:** every monetary field is a **JSON string** holding an exact
  `Decimal` (e.g. `"199.99"`) — stored as TEXT in SQLite, never a float.
- **Dates:** ISO `YYYY-MM-DD`. **Errors:** FastAPI-standard
  `{"detail": "..."}`.
- **Interactive docs:** `/docs` (Swagger UI), `/redoc`, `/openapi.json`.

---

## 0. Meta

| Method | Path | Response |
|--------|------|----------|
| GET | `/` | `{"service":"LedgerMind","version":"1.0.0","docs":"/docs","health":"/health"}` |
| GET | `/health` | `{"status":"ok","version":"1.0.0"}` (readiness probe) |
| GET | `/api/v1/system/config` | `{"db_path", "reconciliation_window_days", "confidence_threshold", "default_user"}` — effective runtime config |

---

## 1. Chart of accounts — `/api/v1/accounts`

`Account` = `{id, code, name, type, normal_balance, is_active, created_at, updated_at}`

- `type`: `asset | liability | equity | income | expense`
- `normal_balance`: `debit | credit`

| Method | Path | Body / params | Success | Errors |
|--------|------|---------------|---------|--------|
| GET | `/api/v1/accounts` | — | `200` `list[Account]` (17 seeded) | — |
| POST | `/api/v1/accounts` | `AccountCreate`: `{code*, name*, type*, normal_balance*, is_active=true}` | `201` `Account` | `409` duplicate `code`; `422` |
| GET | `/api/v1/accounts/{id}` | — | `200` `{"account": Account, "balance": "0.00"}` (2dp string) | `404` |
| GET | `/api/v1/accounts/{id}/ledger` | — | `200` list of posted journal lines touching the account | `404` |

```bash
curl -X POST http://localhost:8000/api/v1/accounts \
  -H 'Content-Type: application/json' -H 'X-User: controller@example.com' \
  -d '{"code":"5700","name":"Legal & Professional Fees","type":"expense","normal_balance":"debit"}'
```

---

## 2. Journal entries (double-entry GL) — `/api/v1/journal-entries`

`JournalEntry` = `{id, entry_no, memo, entry_date, status, created_by, created_at, posted_at, lines[]}` where each line is `{id, entry_id, account_id, side, amount, account_code, account_name}`.

| Method | Path | Body / params | Success | Errors |
|--------|------|---------------|---------|--------|
| GET | `/api/v1/journal-entries` | query `?status=draft|posted`, `?limit=200`, `?offset=0` | `200` `list[JournalEntry]` (each embeds `lines`) | — |
| POST | `/api/v1/journal-entries` | `JournalEntryCreate` (below) | `201` `JournalEntry` | `404` unknown `account_id`; `422` unbalanced / non-positive amount / <2 lines |
| GET | `/api/v1/journal-entries/{id}` | — | `200` `JournalEntry` | `404` |
| POST | `/api/v1/journal-entries/{id}/post` | optional body `{"actor":"..."}` | `200` `JournalEntry` with `posted_at` stamped | `404` |

`JournalEntryCreate` — **validated before any SQL** (unbalanced ⇒ `422`):

```json
{
  "memo": "Reimburse travel",
  "entry_date": "2026-11-15",
  "lines": [
    {"account_id": 15, "side": "debit",  "amount": "125.50"},
    {"account_id": 2,  "side": "credit", "amount": "125.50"}
  ],
  "status": "draft"
}
```

- `lines`: min 2, max 64; each `amount` must be `> 0` with ≤2 decimal places;
  `sum(debits) == sum(credits)` is enforced by a Pydantic `field_validator`.
- `status` defaults to `"posted"` in the schema; pass `"draft"` then
  `POST .../post` for a draft → post workflow.

---

## 3. Document pipeline — `/api/v1/documents/*`

The headline automation: ingest → extract → confidence → validate →
auto-post **or** exception queue.

### 3.1 `POST /api/v1/documents/ingest` → `201 DocumentResult`

`DocumentIngest` body:

```json
{
  "doc_type": "invoice",                 // invoice | receipt | po
  "memo": "aws cloud hosting monthly service",
  "amount": "199.99",                    // > 0, 2dp
  "entry_date": "2026-11-15",            // not in the future
  "vendor_name": "Acme Supplies",        // optional; auto-created if unknown
  "reference": "INV-4471",               // optional
  "source": "inbox"                      // optional
}
```

`DocumentResult` response:

```json
{
  "id": 1, "doc_type": "invoice", "reference": "INV-4471", "vendor": "Acme Supplies",
  "memo": "aws cloud hosting monthly service", "amount": "199.99",
  "entry_date": "2026-11-15", "confidence": "1.00", "status": "posted",
  "matched_account_code": "5500", "matched_account_name": "Software & Subscriptions",
  "entry_id": 4, "reasons": []
}
```

- `status`: `posted` (auto-posted balanced entry: matched account vs Bank
  `1100`; `entry_id` set) **or** `exception` (`entry_id: null`, `reasons[]`
  explains, e.g. `"confidence 0.00 below threshold 0.80"`).
- Confidence weights: rule matched +0.55, known vendor +0.25, memo ≥4 chars
  +0.10, positive amount +0.10 (max 1.00). Gate: valid **and**
  `confidence >= LEDGERMIND_CONFIDENCE_THRESHOLD` (default `0.80`).

### 3.2 Queue inspection

| Method | Path | Params | Response |
|--------|------|--------|----------|
| GET | `/api/v1/documents` | `?status=queued|posted|exception`, `?limit=200`, `?offset=0` | `200` `list[Document]` |
| GET | `/api/v1/documents/{id}` | — | `200` `Document` | `404` |

### 3.3 Categorisation rules — `/api/v1/categorization-rules`

| Method | Path | Body | Success | Errors |
|--------|------|------|---------|--------|
| GET | `/api/v1/categorization-rules` | — | `200` list (27 seeded) | — |
| POST | `/api/v1/categorization-rules` | `{keyword*, account_code*, priority=100, match_mode="contains"}` | `201` | `400` unknown `account_code`; `422` |

Matching is case-insensitive `contains`; lowest `priority` wins; no match
falls back to Sales Revenue `4000`.

### 3.4 Vendor master — `/api/v1/vendors`

| Method | Path | Response |
|--------|------|----------|
| GET | `/api/v1/vendors` | `200` list of `{id, name, pan, created_at}` (2 seeded) |

---

## 4. Bank-statement reconciliation — `/api/v1/statements/*`

### 4.1 `POST /api/v1/statements/import` → `201`

```json
{
  "source_name": "HDFC Bank Nov",
  "lines": [
    {"amount": "2500.00", "direction": "out", "memo": "office rent", "posted_on": "2026-01-07"},
    {"amount": "90000.00", "direction": "out", "memo": "unknown transfer", "posted_on": "2026-01-08"}
  ]
}
```

`direction`: `in | out`. `lines`: min 1.

### 4.2 `POST /api/v1/statements/{id}/reconcile` → `200 list[MatchResult]`

Matches each line against **posted** journal lines within
`LEDGERMIND_RECON_WINDOW` days (default 3) by amount and date; memo-similarity
breaks ties. `MatchResult`:

```json
{"line_index": 0, "memo": "office rent", "amount": "2500.00",
 "matched": true, "entry_id": 2, "entry_no": "JE-0002", "account_code": "1100",
 "reason": null}
```

`404` if the statement does not exist.

### 4.3 `GET /api/v1/statements?limit=50` → `200` list of imported statements.

---

## 5. Audit trail — `/api/v1/audit`

| Method | Path | Params | Response |
|--------|------|--------|----------|
| GET | `/api/v1/audit` | `?entity=accounts|journal_entries|documents|...`, `?limit=200`, `?offset=0` | `200` `list[AuditRecord]` |

`AuditRecord`: `{id, actor, action, entity, entity_id, detail, created_at}`.
Append-only — rows are only ever inserted (auto-posts log `action:"auto_post"`
on `entity:"documents"`), so any posted document can be reconstructed
end-to-end.

---

## 6. Error reference

| Code | Meaning | Typical trigger |
|------|---------|-----------------|
| `400` | bad request | categorisation rule with unknown `account_code` |
| `404` | not found | unknown account / journal entry / document / statement id |
| `409` | conflict | duplicate account `code` |
| `422` | validation failed | unbalanced journal entry, non-positive amount, future `entry_date`, bad enum |

---

## 7. Seeded demo data (first boot)

| Table | Rows | Content |
|-------|------|---------|
| `accounts` | 17 | Cash `1000`, Bank `1100`, AR `1200`, AP `2000`, loan `2100`, equity `3000`, retained earnings `3100`, sales `4000`, service `4100`, interest `4300`, COGS `5000`, salaries `5100`, rent `5200`, utilities `5300`, travel `5400`, software `5500`, marketing `5600` |
| `categorization_rules` | 27 | e.g. `RENT→5200`, `AWS/SUBSCRIPTION/SOFTWARE→5500`, `SALARY/PAYROLL→5100`, `MARKETING/ADS→5600` |
| `vendors` | 2 | Acme Supplies, Vertex Solutions |
| `journal_entries` / `journal_lines` | 3 / 6 | posted 2026-01-05/06/10: owner capital, January rent ₹2,500.00, consulting revenue (reconciliation demo targets) |

*© SHIVAM ITCS — LedgerMind v1.0.0. Proprietary / internal.*
