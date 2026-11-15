"""Chart-of-accounts, journal entries and general ledger endpoints.

`/api/v1/accounts`      -- manage the chart of accounts, read balances/ledger
`/api/v1/journal-entries` -- create, list and post double-entry journal entries
"""

from __future__ import annotations

import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import config, db, models
from ..schemas import (Account, AccountCreate, AuditContext, EntryStatus,
                       JournalEntry, JournalEntryCreate, PostJournalRequest)
from .deps import get_audit, get_db

router = APIRouter(prefix="/api/v1", tags=["accounting"])


class BalanceView(BaseModel):
    account: Account
    balance: str  # formatted to 2dp Decimal


# --------------------------------------------------------------------------- #
# Accounts / chart of accounts
# --------------------------------------------------------------------------- #

@router.get("/accounts", response_model=list[Account])
def list_accounts(conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    return models.list_accounts(conn)


@router.post("/accounts", response_model=Account, status_code=201)
def create_account(payload: AccountCreate, audit: AuditContext = Depends(get_audit),
                   conn: sqlite3.Connection = Depends(get_db)) -> dict:
    if db.fetch_id_by(conn, "accounts", code=payload.code):
        raise HTTPException(status_code=409, detail=f"account code already exists: {payload.code}")
    return models.create_account(
        conn, code=payload.code, name=payload.name, atype=payload.type,
        normal_balance=payload.normal_balance, is_active=payload.is_active,
        actor=audit.actor,
    )


@router.get("/accounts/{account_id}", response_model=BalanceView)
def get_account(account_id: int, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    account = models.get_account(conn, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="account not found")
    return {"account": account, "balance": f"{models.balance_of(conn, account_id):.2f}"}


@router.get("/accounts/{account_id}/ledger", response_model=list[dict])
def get_ledger(account_id: int, conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    if not models.get_account(conn, account_id):
        raise HTTPException(status_code=404, detail="account not found")
    return models.ledger(conn, account_id)


# --------------------------------------------------------------------------- #
# Journal entries / GL
# --------------------------------------------------------------------------- #

@router.get("/journal-entries", response_model=list[JournalEntry])
def list_entries(status: EntryStatus | None = None, limit: int = 200, offset: int = 0,
                 conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    return models.list_journal_entries(conn, status=status, limit=limit, offset=offset)


@router.post("/journal-entries", response_model=JournalEntry, status_code=201)
def create_entry(payload: JournalEntryCreate, audit: AuditContext = Depends(get_audit),
                 conn: sqlite3.Connection = Depends(get_db)) -> dict:
    for line in payload.lines:
        if not models.get_account(conn, line.account_id):
            raise HTTPException(status_code=404, detail=f"account {line.account_id} not found")
    lines = [(l.account_id, l.side, f"{l.amount:.2f}") for l in payload.lines]
    return models.create_journal_entry(
        conn, memo=payload.memo, entry_date=payload.entry_date, lines=lines,
        status=payload.status, actor=audit.actor,
    )


@router.get("/journal-entries/{entry_id}", response_model=JournalEntry)
def get_entry(entry_id: int, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    entry = models.get_journal_entry(conn, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="journal entry not found")
    return entry


@router.post("/journal-entries/{entry_id}/post", response_model=JournalEntry)
def post_entry(entry_id: int, payload: PostJournalRequest | None = None,
               audit: AuditContext = Depends(get_audit),
               conn: sqlite3.Connection = Depends(get_db)) -> dict:
    entry = models.post_journal_entry(conn, entry_id, actor=payload.actor or audit.actor)
    if not entry:
        raise HTTPException(status_code=404, detail="journal entry not found")
    return entry


# --- Misc: config introspection ------------------------------------------- #

@router.get("/system/config")
def system_config(audit: AuditContext = Depends(get_audit)) -> dict:
    return {
        "db_path": str(config.DB_PATH),
        "reconciliation_window_days": config.RECONCILIATION_WINDOW_DAYS,
        "confidence_threshold": config.CONFIDENCE_THRESHOLD,
        "default_user": config.DEFAULT_USER,
    }