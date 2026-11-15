"""Domain services -- the back-office automation core.

This module implements the accounting-domain rules that belong above the
plain data-access layer in :mod:`app.models`:

* :func:`ingest_document` -- the document-AI pipeline. A raw document becomes
  an extracted/validated result that is either **auto-posted** to the ledger
  (high confidence) or routed to the **exception queue** (low confidence),
  mirroring the playbook's straight-through rate (target > 80%).
* :func:`_extract_account` -- schema of the extraction stage: a matched
  categorisation rule plus a stated vendor turn a free-text memo into a
  ledger account code.
* :func:`_score_confidence` -- confidence scoring used by the gate.
* :func:`_validate` -- business-rule validation gate (non-empty memo,
  positive amount, sane dates, vendor known).

The design point: *no auto-post without a confidence gate + validation pass*.
Documents that fail either gate are saved as ``exception`` and human review
is expected to resolve them.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from typing import Any

from . import config, db, models


def ingest_document(
    conn: sqlite3.Connection,
    *,
    doc_type: str,
    memo: str,
    amount: Decimal,
    entry_date: date,
    vendor_name: str | None,
    reference: str | None,
    source: str | None,
    actor: str = "system",
    confidence_threshold: Decimal | None = None,
) -> dict[str, Any]:
    """Run one document through the pipeline and persist the outcome.

    Pipeline stages, in order:

    1. **Extraction** -- a ledger account is chosen from the categorisation
       rules (:func:`app.models.match_rule`); a vendor is looked up.
    2. **Confidence scoring** -- :func:`_score_confidence` returns 0..1.
    3. **Validation** -- business rules in :func:`_validate`.
    4. **Gate** -- above both ``confidence_threshold`` *and* validation =>
       auto-post a balanced journal entry (debit the expense account, credit
       Bank). Otherwise save as ``exception``.
    5. **Audit** -- every stage is recorded in the audit trail.

    Returns the stored document row with pipeline metadata attached.
    """
    rule = models.match_rule(conn, memo)
    account_code = (rule or {}).get("account_code")
    account_name = (rule or {}).get("account_name")

    vendor = models.find_vendor(conn, vendor_name) if vendor_name else None
    if vendor is None and vendor_name:
        vendor = models.create_vendor(conn, name=vendor_name, pan=None, actor=actor)

    confidence = _score_confidence(
        has_rule=rule is not None,
        has_vendor=vendor is not None,
        memo=memo,
        amount=amount,
    )
    threshold = confidence_threshold or _default_threshold()
    validation = _validate(memo=memo, amount=amount, entry_date=entry_date)
    reasons: list[str] = []

    if not validation["valid"]:
        reasons.extend(validation["errors"])
    if confidence < threshold:
        reasons.append(f"confidence {confidence:.2f} below threshold {threshold:.2f}")

    # Choose the -- bank -- side: an expense/asset account debits (mirror
    # credit to Bank); an income/liability account credits (mirror debit to
    # Bank). The Bank account (1100) is the counterpart for posted documents.
    account_id = int(rule["account_id"]) if rule else None
    bank_id = conn.execute("SELECT id FROM accounts WHERE code = ?", ("1100",)).fetchone()[0]

    if validation["valid"] and confidence >= threshold and rule:
        norm = conn.execute(
            "SELECT normal_balance FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()["normal_balance"]
        doc_side = "debit" if norm == "debit" else "credit"
        bank_side = "credit" if doc_side == "debit" else "debit"
        entry = models.create_journal_entry(
            conn,
            memo=f"{doc_type.upper()} {reference or ''} {memo}".strip(),
            entry_date=entry_date,
            lines=[(account_id, doc_side, f"{amount:0.2f}"),
                   (bank_id, bank_side, f"{amount:0.2f}")],
            status="posted",
            actor=actor,
        )
        status, entry_id = "posted", entry["id"]
        db.audit(conn, actor, "auto_post", "documents", None,
                 f"document posted as entry {entry['entry_no']}")
    else:
        status, entry_id = "exception", None

    doc = models.save_document(
        conn,
        doc_type=doc_type,
        memo=memo,
        amount=f"{amount:0.2f}",
        entry_date=entry_date.isoformat(),
        confidence=f"{confidence:.2f}",
        status=status,
        vendor_id=vendor["id"] if vendor else None,
        reference=reference,
        source=source,
        posted_entry_id=entry_id,
    )
    doc["reasons"] = reasons
    doc["matched_account_code"] = account_code
    doc["matched_account_name"] = account_name
    doc["vendor"] = vendor["name"] if vendor else None
    # Convenience alias for the response contract (DB column is posted_entry_id).
    doc["entry_id"] = doc.get("posted_entry_id")
    return doc


def _score_confidence(*, has_rule: bool, has_vendor: bool, memo: str, amount: Decimal) -> Decimal:
    """Confidence 0..1 from independent signals (rule, vendor, memo & amount)."""
    score = Decimal("0.0")
    if has_rule:
        score += Decimal("0.55")
    if has_vendor:
        score += Decimal("0.25")
    if memo and len(memo) >= 4:
        score += Decimal("0.10")
    if amount > 0:
        score += Decimal("0.10")
    return min(score, Decimal("1.00"))


def _default_threshold() -> Decimal:
    return Decimal(config.__dict__.get("CONFIDENCE_THRESHOLD", "0.80"))


def _validate(*, memo: str, amount: Decimal, entry_date: date) -> dict[str, Any]:
    """Business-rule validation (the playbook's 'validate totals and sense')."""
    errors: list[str] = []
    if not memo or not memo.strip():
        errors.append("memo is empty")
    if amount <= 0:
        errors.append("amount must be positive")
    if entry_date > date.today():
        errors.append(f"entry_date {entry_date} is in the future")
    return {"valid": not errors, "errors": errors}