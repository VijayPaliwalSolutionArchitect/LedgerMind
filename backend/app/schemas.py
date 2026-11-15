"""Pydantic schemas (API contracts).

Pydantic v2 models that sit between the HTTP layer and the persistence
layer. All money fields are declared as ``Decimal`` so values round-trip as
exact decimals -- matching the decision in :mod:`app.db` to store money as
TEXT in SQLite. Schemas validate *shape and sense* (an amount must be
positive, a journal entry must balance) before anything reaches SQL.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --------------------------------------------------------------------------- #
# Shared primitives
# --------------------------------------------------------------------------- #

AccountType = Literal["asset", "liability", "equity", "income", "expense"]
Side = Literal["debit", "credit"]
EntryStatus = Literal["draft", "posted"]
DocumentStatus = Literal["queued", "posted", "exception"]
DocumentType = Literal["invoice", "receipt", "po"]


class AuditContext(BaseModel):
    """Per-request actor identity, defaulting to the configured user.

    The ``actor`` is stamped into the audit trail for every mutation. It can
    be overridden per request via the ``X-User`` header (see main.py).
    """

    actor: str = Field(default="system", max_length=64)


# --------------------------------------------------------------------------- #
# Accounts (chart of accounts)
# --------------------------------------------------------------------------- #

class AccountCreate(BaseModel):
    code: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=128)
    type: AccountType
    normal_balance: Literal["debit", "credit"]
    is_active: bool = True


class Account(AccountCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: str
    updated_at: str


# --------------------------------------------------------------------------- #
# Journal entries & lines
# --------------------------------------------------------------------------- #

class JournalLineIn(BaseModel):
    account_id: int
    side: Side
    amount: Decimal = Field(gt=0, decimal_places=2)


class JournalEntryCreate(BaseModel):
    memo: str = Field(min_length=1, max_length=256)
    entry_date: date
    lines: list[JournalLineIn] = Field(min_length=2, max_length=64)
    status: EntryStatus = "posted"

    @field_validator("lines")
    @classmethod
    def _must_balance(cls, lines: list[JournalLineIn]) -> list[JournalLineIn]:
        """Require the debits to equal the credits (double-entry invariant)."""
        total = sum((l.amount if l.side == "debit" else -l.amount) for l in lines)
        if total != 0:
            raise ValueError("journal entry is unbalanced (debits must equal credits)")
        return lines


class JournalLine(JournalLineIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    entry_id: int
    account_code: str | None = None
    account_name: str | None = None


class JournalEntry(JournalEntryCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    entry_no: str
    created_by: str
    created_at: str
    posted_at: str | None = None
    lines: list[JournalLine] = Field(default_factory=list)


class PostJournalRequest(BaseModel):
    """Body for promoting a draft journal entry to posted."""

    actor: str | None = None


# --------------------------------------------------------------------------- #
# Categorisation rules (auto-classification of transactions)
# --------------------------------------------------------------------------- #

class CategorizationRuleCreate(BaseModel):
    keyword: str = Field(min_length=1, max_length=64)
    account_code: str = Field(min_length=1, max_length=16)
    priority: int = Field(default=100, ge=1)
    match_mode: Literal["contains", "equals"] = "contains"


class CategorizationRule(CategorizationRuleCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    account_id: int
    is_active: bool = True
    created_at: str


# --------------------------------------------------------------------------- #
# Documents (back-office document pipeline)
# --------------------------------------------------------------------------- #

class DocumentIngest(BaseModel):
    """A raw document entering the pipeline: memo text, amount, date, type.

    This is the *document AI* entry point -- a stated vendor + free-text memo
    is enough for the pipeline to extract fields, score confidence and either
    auto-post or raise an exception.
    """

    doc_type: DocumentType = "invoice"
    memo: str = Field(min_length=1, max_length=512)
    amount: Decimal = Field(gt=0, decimal_places=2)
    entry_date: date
    vendor_name: str | None = Field(default=None, max_length=128)
    reference: str | None = Field(default=None, max_length=64)
    source: str | None = Field(default=None, max_length=64)


class DocumentResult(BaseModel):
    """Outcome of running one document through the pipeline."""

    id: int
    doc_type: str
    reference: str | None
    vendor: str | None
    memo: str
    amount: Decimal
    entry_date: str
    confidence: Decimal
    status: DocumentStatus
    matched_account_code: str | None = None
    matched_account_name: str | None = None
    entry_id: int | None = None
    reasons: list[str] = Field(default_factory=list)


class Document(DocumentResult):
    model_config = ConfigDict(from_attributes=True)
    created_at: str


# --------------------------------------------------------------------------- #
# Bank statements & reconciliation
# --------------------------------------------------------------------------- #

class StatementLine(BaseModel):
    """One line of a bank statement, as captured by the ingestion layer."""

    amount: Decimal = Field(decimal_places=2)
    direction: Literal["in", "out"] = "out"
    memo: str = Field(min_length=1, max_length=512)
    posted_on: date


class StatementImport(BaseModel):
    source_name: str | None = Field(default=None, max_length=64)
    lines: list[StatementLine] = Field(min_length=1)


class MatchResult(BaseModel):
    """Result of matching one statement line against posted journal lines."""

    line_index: int
    memo: str
    amount: Decimal
    matched: bool
    entry_id: int | None = None
    entry_no: str | None = None
    account_code: str | None = None
    reason: str | None = None


# --------------------------------------------------------------------------- #
# Audit trail
# --------------------------------------------------------------------------- #

class AuditRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    actor: str
    action: str
    entity: str
    entity_id: str | None
    detail: str | None
    created_at: str


class HealthCheck(BaseModel):
    status: Literal["ok"]
    version: str
    database: str