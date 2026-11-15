"""Application configuration.

Centralises path and environment configuration for LedgerMind.
Uses environment variables with sensible defaults so the app runs
out-of-the-box while remaining configurable for deployments.
"""

from __future__ import annotations

import os
from pathlib import Path

# Project layout: backend/app/config.py contains app; project root is two levels up.
BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent

# The SQLite database file lives in backend/data by default. An absolute or
# relative path may be supplied via the LEDGERMIND_DB_PATH environment variable
# (used by tests to point at a throwaway database).
DB_PATH = Path(
    os.environ.get("LEDGERMIND_DB_PATH", str(BACKEND_DIR / "data" / "ledgermind.db"))
)

# Data directory is created automatically if missing.
DATA_DIR = DB_PATH.resolve().parent

# Matching window (in days) used by bank-statement reconciliation.
RECONCILIATION_WINDOW_DAYS = int(os.environ.get("LEDGERMIND_RECON_WINDOW", "3"))

# Minimum confidence score (0..1) for the document pipeline to auto-post a
# document to the ledger instead of routing it to the exception queue.
# Overridable via LEDGERMIND_CONFIDENCE_THRESHOLD.
CONFIDENCE_THRESHOLD = os.environ.get("LEDGERMIND_CONFIDENCE_THRESHOLD", "0.80")

# Actor name stamped into the audit trail when a request with a running user is
# made. Overridable via header \"X-User\" per request (see schemas.AuditContext).
DEFAULT_USER = os.environ.get("LEDGERMIND_DEFAULT_USER", "system")