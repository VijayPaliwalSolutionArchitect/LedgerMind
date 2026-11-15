"""Document & back-office automation endpoints (the document-AI pipeline).

`/api/v1/documents/ingest`    -- push a raw document through the pipeline:
                                extract -> validate -> confidence gate ->
                                auto-post or exception queue.
`/api/v1/documents`           -- inspect the queue (posted / exception).
`/api/v1/categorization-rules`-- manage the rules that map memos to accounts.
`/api/v1/vendors`             -- vendor-master matching.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from .. import models, services
from ..schemas import (AuditContext, CategorizationRule, CategorizationRuleCreate,
                       Document, DocumentIngest, DocumentResult, DocumentStatus)
from .deps import get_audit, get_db

router = APIRouter(prefix="/api/v1", tags=["documents"])


# --------------------------------------------------------------------------- #
# Documents / back-office pipeline
# --------------------------------------------------------------------------- #

@router.post("/documents/ingest", response_model=DocumentResult, status_code=201)
def ingest_document(payload: DocumentIngest, audit: AuditContext = Depends(get_audit),
                    conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """Run one document through extraction + validation + the confidence gate."""
    return services.ingest_document(
        conn,
        doc_type=payload.doc_type,
        memo=payload.memo,
        amount=payload.amount,
        entry_date=payload.entry_date,
        vendor_name=payload.vendor_name,
        reference=payload.reference,
        source=payload.source,
        actor=audit.actor,
    )


@router.get("/documents", response_model=list[Document])
def list_documents(status: DocumentStatus | None = None, limit: int = 200, offset: int = 0,
                   conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    return models.list_documents(conn, status=status, limit=limit, offset=offset)


@router.get("/documents/{doc_id}", response_model=Document)
def get_document(doc_id: int, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    doc = models.get_document(conn, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    return doc


# --------------------------------------------------------------------------- #
# Categorisation rules
# --------------------------------------------------------------------------- #

@router.get("/categorization-rules", response_model=list[CategorizationRule])
def list_rules(conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    return models.list_rules(conn)


@router.post("/categorization-rules", response_model=CategorizationRule, status_code=201)
def create_rule(payload: CategorizationRuleCreate, audit: AuditContext = Depends(get_audit),
                conn: sqlite3.Connection = Depends(get_db)) -> dict:
    try:
        return models.create_rule(
            conn, keyword=payload.keyword, account_code=payload.account_code,
            priority=payload.priority, match_mode=payload.match_mode, actor=audit.actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Vendor master
# --------------------------------------------------------------------------- #

@router.get("/vendors", response_model=list[dict])
def list_vendors(conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    return models.list_vendors(conn)