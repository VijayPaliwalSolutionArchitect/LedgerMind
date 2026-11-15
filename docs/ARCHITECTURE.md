# Architecture

*Version: v0.1 · 15-11-26 · Author: SHIVAM ITCS (JARVIS)*

LedgerMind is a layered FastAPI application: **HTTP → validation → services →
data-access → SQLite**, with an append-only audit trail wired through every
mutation so the system is fully reconstructable.

```
                        ┌────────────────────────────────────────────┐
                        │             HTTP (FastAPI / uvicorn)        │
                        │  main.py  ·  routers/ (thin, SQL-free)      │
                        └───────────────┬────────────────────────────┘
                                        │ Pydantic v2 (schemas.py)
                                        │  Decimal money · balance checks
                        ┌───────────────▼────────────────────────────┐
                        │             services.py                     │
                        │  document pipeline: extract → confidence → │
                        │  validate → auto-post | exception           │
                        └───────────────┬────────────────────────────┘
                                        │ plain dicts
                        ┌───────────────▼────────────────────────────┐
                        │             models.py (data access)         │
                        │  explicit SQL · every query owned here      │
                        └───────────────┬────────────────────────────┘
                                        │ sqlite3
                        ┌───────────────▼────────────────────────────┐
                        │          SQLite (backend/data/app.db)       │
                        │ 9 tables · money stored as TEXT             │
                        └────────────────────────────────────────────┘
```

## Layer responsibilities

### 1. HTTP layer — `app.main` + `app.routers/*`
- `main.py` builds the `FastAPI` application, attaches a **lifespan** hook that
  bootstraps the database (idempotent schema + seed), and mounts four domain
  routers under `/api/v1`.
- Each router is a pure presentation layer: it parses/validates the request
  body through a Pydantic schema, calls into `app.models` / `app.services`,
  and serialises the result. **No SQL and no business rules live here.**
- `deps.py` provides two DI dependencies:
  - `get_db` — yields a request-scoped `sqlite3.Connection` (commits on
    success, always closes);
  - `get_audit` — resolves the auditing actor, honouring the `X-User` header.

### 2. Validation layer — `app.schemas.py`
All API bodies/responses are Pydantic v2 models. Validation is **two-stage**:
- **shape** — required fields, enums, `Decimal` parsing;
- **sense** — custom `model_validator`s enforce accounting invariants such as
  *a journal entry's debits must equal its credits* and *amounts must be
  positive*.

Because this runs before any SQL, invalid payloads never touch the database.

### 3. Domain services — `app.services.py`
Holds the one piece of genuine business logic: the document pipeline in
`ingest_document()`. It
1. **extracts** an account by matching the memo against categorisation rules
   and a vendor from the vendor master;
2. **scores confidence** (0..1) from independent signals — a matched rule, a
   known vendor, a non-trivial memo, a positive amount;
3. **validates** business rules (non-empty memo, positive amount, not a future
   date);
4. **gates** — only if validation passes *and* confidence ≥ the configured
   threshold (default `0.80`) does it **auto-post** a balanced journal entry
   (matched account vs. Bank `1100`); otherwise it saves the document as
   **`exception`** with machine-readable reasons;
5. **audits** every stage.

### 4. Data-access layer — `app.models.py`
A thin, explicit SQL layer. Every function takes a `sqlite3.Connection` and
returns plain `dict`/`list[dict]`, keeping the HTTP and service layers free of
SQL. Key functions cover accounts, journal entries & lines, ledger/balances,
categorisation rules, vendors, documents, statements and reconciliation, and
audit reads.

### 5. Persistence — `app.db.py`
Owns connection lifecycle (`connect`, WAL mode, foreign keys, dict-style rows),
schema creation (`init_schema`), the `audit()` primitive, the idempotent
`seed()`, and `ensure_schema_and_seed()` used by the app and the seed CLI.

## Money as TEXT

**Every monetary value (`amount`, `balance`) is stored as `TEXT` in SQLite.**
Values are parsed to `Decimal` only at the API boundary (in `schemas.py`;
`services.py`/`models.py` convert on the way in), and formatted back to strings
on the way out.

Why:
- Binary floats (`sqlite3`'s default `REAL`) drift on repeated arithmetic
  (`0.1 + 0.2 != 0.3`).
- Accounting demands exactness; a false penny in a ledger is a real defect.
- `Decimal` round-trips losslessly with the canonical `"123.45"` text form.

Balances are computed by summing the signed line amounts with `Decimal`
arithmetic in `models.py`, never by trusting stored totals.

## The audit trail (reconstructability)

`audit_log` is **append-only** — rows are only ever inserted, never updated or
deleted. Every mutation (account changes, journal entries, document
ingestions, posting, imports, matches, seeds) writes an
`(actor, action, entity, entity_id, detail, created_at)` row. This supports
the playbook guarantee that a posted document can be traced end-to-end:
document image → extraction → validation → confidence gate → approver →
posting → journal line.

## The document pipeline

```
                    ┌───────────┐
Document  ─────────►│ extract   │  memo → rule → account · vendor master
                    └─────┬─────┘
                          ▼
                    ┌───────────┐
                    │ confidence│  0.55 rule + 0.25 vendor + 0.10 memo + 0.10 amount
                    └─────┬─────┘
                          ▼
                    ┌───────────┐
                    │ validate  │  non-empty memo · amount > 0 · not future-dated
                    └─────┬─────┘
                          ▼
                 valid AND conf ≥ 0.80?
                    /            \
              yes /                  \ no
                 ▼                    ▼
          auto-post balanced     exception queue
          (account vs Bank 1100)   + reasons
```

This gives the playbook's **straight-through rate**: the majority of documents
(with a rule match and a known vendor) post automatically; the rest are
shortlist-able exceptions an approver can resolve.

## Data model

Eight application tables (plus SQLite's internal `sqlite_sequence`):

| Table | Purpose | Notable columns |
|---|---|---|
| `accounts` | Chart of accounts | `code` (unique), `name`, `type` (`asset`/`liability`/`equity`/`income`/`expense`), `normal_balance` (`debit`/`credit`), `is_active` |
| `journal_entries` | Header of a posted/draft entry | `entry_no`, `memo`, `entry_date`, `status` (`draft`/`posted`), `created_by`, `posted_at` |
| `journal_lines` | Double-entry lines | `entry_id` → `journal_entries`, `account_id` → `accounts`, `side` (`debit`/`credit`), `amount` (TEXT), `manual_override` |
| `categorization_rules` | Memo → account mapping | `keyword` (uppercase), `account_id` → `accounts`, `priority`, `match_mode` (`contains`), `is_active` |
| `vendors` | Vendor master | `name` (unique), `pan` |
| `documents` | Back-office queue | `doc_type` (`invoice`/`receipt`/`po`), `reference`, `vendor_id`, `memo`, `amount` (TEXT), `entry_date`, `confidence` (TEXT), `status` (`queued`/`posted`/`exception`), `source`, `posted_entry_id` → `journal_entries` |
| `statements` | Imported bank statements | `source_name`, `statement_json` (JSON lines) |
| `audit_log` | Append-only trail | `actor`, `action`, `entity`, `entity_id`, `detail`, `created_at` |

Key integrity rules enforced in schema or code:
- `journal_lines.entry_id` and `documents.posted_entry_id` cascade-on-delete.
- A journal entry must balance (`sum(debit) == sum(credit)`), enforced by a
  Pydantic validator before insert *and* by `create_journal_entry`.
- Column CHECKs pin `documents.status` to `('posted','exception','queued')`.

## Seed data (idempotent)

On first boot `db.seed()` populates, guarded by row-count checks so re-runs are
no-ops:

- **17 accounts** spanning all five types (Cash `1000`, Bank `1100`, AR `1200`,
  AP `2000`, loan `2100`, equity `3000`, retained earnings `3100`, sales `4000`,
  service `4100`, interest `4300`, COGS `5000`, salaries `5100`, rent `5200`,
  utilities `5300`, travel `5400`, software `5500`, marketing `5600`);
- **27 categorisation rules** at priorities 100/110/120 (e.g. `RENT`→5200,
  `AWS`/`SUBSCRIPTION`/`SOFTWARE`→5500, `SALARY`/`PAYROLL`/`WAGES`→5100,
  `MARKETING`/`ADS`/`FACEBOOK`/`GOOGLE ADS`→5600);
- **2 vendors**: Acme Supplies (`ACMPA1234A`), Vertex Solutions (`VTXPA5678B`);
- **3 posted journal entries** dated 2026-01-05/06/10: owner capital injection,
  January office rent (₹2,500.00), consulting revenue — which double as
  realistic fixtures for reconciliation.

## Reconciliation

Bank-statement lines are matched to **posted** journal entries within a
reconciliation window (`RECONCILIATION_WINDOW_DAYS`, default 3) by amount and
date; a memo-similarity score breaks ties. A matched line reports
`matched=True` plus the matched entry/account; an unmatched line stays open.
The seeded rent entry is a ready-made target for demos.