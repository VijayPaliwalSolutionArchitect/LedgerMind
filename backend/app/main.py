"""LedgerMind FastAPI application entrypoint.

Run from the repository root (or ``backend/``)::

    uvicorn app.main:app --reload        # inside backend/
    python -m app.main                    # convenience launcher

On startup the app connects to the configured SQLite database and idempotently
creates the schema and seed data (see :mod:`app.db`). The API is versioned
under ``/api/v1`` and grouped by domain router:

* **/api/v1/accounts** & **/journal-entries**  -- general ledger
* **/api/v1/documents/** & **/categorization-rules** -- back-office pipeline
* **/api/v1/statements/**  -- bank-statement reconciliation
* **/api/v1/audit**  -- immutable audit trail

Interactive docs are served by FastAPI at ``/docs`` and ``/redoc``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import __version__, config, db
from .routers import accounting, audit, documents, reconciliation

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ledgermind")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Boot the database (schema + idempotent seed) once when the app starts."""
    db_path = config.DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = db.ensure_schema_and_seed(db_path)
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("accounts", "vendors", "categorization_rules", "journal_entries")
    }
    conn.close()
    logger.info("LedgerMind %s ready @ %s (accounts=%s vendors=%s rules=%s entries=%s)",
                __version__, db_path, counts["accounts"], counts["vendors"],
                counts["categorization_rules"], counts["journal_entries"])
    yield


app = FastAPI(
    title="LedgerMind API",
    description=(
        "AI-enabled double-entry bookkeeping / general ledger, with a "
        "document & back-office automation pipeline: extract -> validate -> "
        "confidence gate -> auto-post or exception queue."
    ),
    version=__version__,
    lifespan=lifespan,
)


@app.get("/", tags=["meta"])
def root() -> dict:
    return {
        "service": "LedgerMind",
        "version": __version__,
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "version": __version__}

app.include_router(accounting.router)
app.include_router(documents.router)
app.include_router(reconciliation.router)
app.include_router(audit.router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)