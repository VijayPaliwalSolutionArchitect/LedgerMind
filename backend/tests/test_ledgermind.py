"""Integration tests for the LedgerMind API.

Runs against a throwaway SQLite database (``LEDGERMIND_DB_PATH`` points at a
temporary file) through FastAPI's ``TestClient``, so none of the dev data
is touched. Covers the four domains: GL/accounts, the document pipeline,
reconciliation and the audit trail.

Run with:  ``pytest``  (from ``backend/``)
"""

from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Point the app at a throwaway database BEFORE importing it (config is read
# at import time).
_tmp = tempfile.TemporaryDirectory()
os.environ["LEDGERMIND_DB_PATH"] = str(Path(_tmp.name) / "test_ledgermind.db")

from app.main import app  # noqa: E402
from app import db  # noqa: E402

client = TestClient(app)


@pytest.fixture(scope="session", autouse=True)
def _fresh_db():
    """Ensure a clean seeded DB for the whole session."""
    db.ensure_schema_and_seed()
    yield


def _db_path() -> Path:
    return db.config.DB_PATH.resolve()


# --------------------------------------------------------------------------- #
# Boot & seed
# --------------------------------------------------------------------------- #

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_seed_populated():
    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 17
    assert conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM categorization_rules").fetchone()[0] >= 20
    assert conn.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0] == 3
    conn.close()


# --------------------------------------------------------------------------- #
# Accounts / GL
# --------------------------------------------------------------------------- #

def test_list_accounts():
    r = client.get("/api/v1/accounts")
    assert r.status_code == 200
    codes = {a["code"] for a in r.json()}
    assert {"1000", "1100", "3000", "4000", "5000"} <= codes


def test_create_account_and_balance():
    r = client.post("/api/v1/accounts", json={
        "code": "5700", "name": "Legal & Professional Fees",
        "type": "expense", "normal_balance": "debit",
    })
    assert r.status_code == 201, r.text
    account_id = r.json()["id"]
    bk = client.get(f"/api/v1/accounts/{account_id}")
    assert bk.status_code == 200
    assert bk.json()["balance"] == "0.00"


def test_create_account_duplicate_rejected():
    r = client.post("/api/v1/accounts", json={
        "code": "1000", "name": "Copy", "type": "asset", "normal_balance": "debit",
    })
    assert r.status_code == 409


def test_create_journal_entry_balances():
    accounts = {a["code"]: a["id"] for a in client.get("/api/v1/accounts").json()}
    r = client.post("/api/v1/journal-entries", json={
        "memo": "Test transfer",
        "entry_date": date.today().isoformat(),
        "lines": [
            {"account_id": accounts["1100"], "side": "debit", "amount": "125.50"},
            {"account_id": accounts["2000"], "side": "credit", "amount": "125.50"},
        ],
    })
    assert r.status_code == 201, r.text
    entry = r.json()
    assert entry["status"] == "posted"
    assert entry["posted_at"] is not None


def test_unbalanced_entry_rejected():
    accounts = {a["code"]: a["id"] for a in client.get("/api/v1/accounts").json()}
    r = client.post("/api/v1/journal-entries", json={
        "memo": "Should fail",
        "entry_date": date.today().isoformat(),
        "lines": [
            {"account_id": accounts["1100"], "side": "debit", "amount": "100.00"},
            {"account_id": accounts["2000"], "side": "credit", "amount": "90.00"},
        ],
    })
    assert r.status_code == 422  # validation: debits must equal credits


# --------------------------------------------------------------------------- #
# Document & back-office pipeline
# --------------------------------------------------------------------------- #

def test_document_auto_posts_on_high_confidence():
    """Known vendor + matched rule + valid amount => straight-through post."""
    payload = {
        "doc_type": "invoice",
        "memo": "AWS cloud hosting monthly service",
        "amount": "199.99",
        "entry_date": date.today().isoformat(),
        "vendor_name": "Acme Supplies",
        "reference": "INV-4471",
        "source": "inbox",
    }
    r = client.post("/api/v1/documents/ingest", json=payload)
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["status"] == "posted"
    assert float(doc["confidence"]) >= float(os.environ.get("LEDGERMIND_CONFIDENCE_THRESHOLD", "0.80"))
    assert doc["matched_account_code"] == "5500"  # Software & Subscriptions
    assert doc["entry_id"] is not None


def test_document_routes_to_exception_when_unmatched():
    """No matching rule => low confidence => exception queue (no auto-post)."""
    payload = {
        "doc_type": "receipt",
        "memo": "Mystery one-off payment ZZZ",
        "amount": "42.00",
        "entry_date": date.today().isoformat(),
        "source": "inbox",
    }
    r = client.post("/api/v1/documents/ingest", json=payload)
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["status"] == "exception"
    assert doc["entry_id"] is None
    assert doc["reasons"]


def test_list_documents_status_filter():
    posted = client.get("/api/v1/documents", params={"status": "posted"}).json()
    exceptions = client.get("/api/v1/documents", params={"status": "exception"}).json()
    assert all(d["status"] == "posted" for d in posted)
    assert all(d["status"] == "exception" for d in exceptions)


def test_categorization_rule_crud():
    r = client.post("/api/v1/categorization-rules", json={
        "keyword": "OFFICE SUPPLIES", "account_code": "5000", "priority": 90,
    })
    assert r.status_code == 201, r.text
    assert r.json()["account_code"] == "5000"
    # Unknown account -> 400
    r2 = client.post("/api/v1/categorization-rules", json={
        "keyword": "X", "account_code": "9999", "priority": 1,
    })
    assert r2.status_code == 400


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #

def test_statement_import_and_reconcile():
    # The seed posts "January office rent" of 2500.00 on 2026-01-06.
    r = client.post("/api/v1/statements/import", json={
        "source_name": "HDFC Bank Oct",
        "lines": [
            {"amount": "2500.00", "direction": "out", "memo": "office rent",
             "posted_on": "2026-01-07"},
            {"amount": "90000.00", "direction": "out", "memo": "unknown transfer",
             "posted_on": "2026-01-08"},
        ],
    })
    assert r.status_code == 201, r.text
    statement_id = r.json()["id"]

    r = client.post(f"/api/v1/statements/{statement_id}/reconcile")
    assert r.status_code == 200, r.text
    results = {m["line_index"]: m for m in r.json()}
    assert results[0]["matched"] is True
    # The bank statement line represents the cash outflow, so it matches the
    # Bank (1100) side of the balanced rent journal entry.
    assert results[0]["account_code"] == "1100"
    assert results[1]["matched"] is False


# --------------------------------------------------------------------------- #
# Audit trail
# --------------------------------------------------------------------------- #

def test_audit_trail_has_pipeline_events():
    r = client.get("/api/v1/audit")
    assert r.status_code == 200
    actions = {r["action"] for r in r.json()}
    assert {"auto_post", "create", "import"} <= actions


def test_audit_actor_header_stamped():
    r = client.post("/api/v1/accounts", json={
        "code": "5800", "name": "Insurance", "type": "expense", "normal_balance": "debit",
    }, headers={"X-User": "controller@example.com"})
    assert r.status_code == 201, r.text
    r = client.get("/api/v1/audit", params={"entity": "accounts"})
    assert r.status_code == 200
    assert any(a["actor"] == "controller@example.com" for a in r.json())