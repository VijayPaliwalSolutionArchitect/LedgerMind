"""Shared dependency-injection helpers for the routers.

* :func:`get_db` -- yields a live ``sqlite3.Connection`` (auto-committed on
  success) for the lifetime of a request. The same connection backs every
  model call in that request, so multi-step mutations stay consistent.
* :func:`get_audit` -- builds an :class:`app.schemas.AuditContext` honouring
  the optional ``X-User`` header so deployments can stamp the real actor.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from fastapi import Header

from .. import db
from ..schemas import AuditContext


def get_db() -> Iterator[sqlite3.Connection]:
    """Open a request-scoped connection, commit on success, always close."""
    conn = db.connect_app()
    try:
        yield conn
        # The default dependency exits cleanly; mutations are committed by
        # the model layer. Commit here too so all paths leave a consistent DB.
        conn.commit()
    finally:
        conn.close()


def get_audit(x_user: str | None = Header(default=None, alias="X-User")) -> AuditContext:
    """Resolve the actor stamping this request's audit records."""
    return AuditContext(actor=x_user) if x_user else AuditContext()