"""Bank-statement import & reconciliation endpoints.

`/api/v1/statements`   -- list imported statements.
`/api/v1/statements/import` -- ingest a bank statement (JSON lines).
`/api/v1/statements/{id}/reconcile` -- match statement lines to the posted
                            ledger and return per-line match results.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from .. import models
from ..schemas import AuditContext, MatchResult, StatementImport
from .deps import get_audit, get_db

router = APIRouter(prefix="/api/v1", tags=["reconciliation"])


@router.post("/statements/import", status_code=201)
def import_statement(payload: StatementImport, audit: AuditContext = Depends(get_audit),
                     conn: sqlite3.Connection = Depends(get_db)) -> dict:
    lines = [l.model_dump(mode="json") for l in payload.lines]
    return models.store_statement(conn, source_name=payload.source_name, lines=lines, actor=audit.actor)


@router.get("/statements")
def list_statements(limit: int = 50, conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    return models.list_statements(conn, limit=limit)


@router.post("/statements/{statement_id}/reconcile", response_model=list[MatchResult])
def reconcile(statement_id: int, conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    try:
        return models.reconcile_statement(conn, statement_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc