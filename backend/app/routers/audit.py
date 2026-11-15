"""Immutable audit-trail endpoints (reconstructability guarantee).

`/api/v1/audit` -- read the append-only audit log. Every mutation on the
system (account changes, journal entries, document ingestions, posting,
reconciliations, seeds) leaves a trace here so a document can be
reconstructed end-to-end: image -> extraction -> validation -> approver ->
posting.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from .. import models
from ..schemas import AuditRecord
from .deps import get_db

router = APIRouter(prefix="/api/v1", tags=["audit"])


@router.get("/audit", response_model=list[AuditRecord])
def list_audit(entity: str | None = None, limit: int = 200, offset: int = 0,
               conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    return models.list_audit(conn, entity=entity, limit=limit, offset=offset)