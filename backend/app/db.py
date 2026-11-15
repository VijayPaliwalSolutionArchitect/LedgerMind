"""SQLite persistence layer.

Uses Python's built-in ``sqlite3`` module (no ORM). All money is stored as
**text** so precision is preserved exactly and no floating point rounding can
creep in. Connections are opened per-call (simple, thread-safe) and every
mutation is wrapped in a transaction that commits once the statement helper
succeeds.

Schema ownership lives in :func:`init_schema` and idempotent seed data in
:func:`seed`. The DB is initialised (schema + seed) automatically on import of
the FastAPI app.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from . import config

# SQLite ships with NaN/Inf support disabled to keep JSON/DECIMAL honest.
sqlite3.register_adapter(Decimal, lambda d: str(d))
sqlite3.register_converter("decimal", lambda b: Decimal(b.decode("utf-8")))

_SCHEMA_STATEMENTS = """
CREATE TABLE IF NOT EXISTS accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT    NOT NULL UNIQUE,
    name            TEXT    NOT NULL,
    type            TEXT    NOT NULL CHECK (type IN ('asset','liability','equity','income','expense')),
    normal_balance  TEXT    NOT NULL CHECK (normal_balance IN ('debit','credit')),
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS journal_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_no    TEXT    NOT NULL UNIQUE,
    memo        TEXT    NOT NULL,
    entry_date  TEXT    NOT NULL,
    status      TEXT    NOT NULL CHECK (status IN ('draft','posted')),
    created_by  TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    posted_at   TEXT
);

CREATE TABLE IF NOT EXISTS journal_lines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id    INTEGER NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
    account_id  INTEGER NOT NULL REFERENCES accounts(id),
    side        TEXT    NOT NULL CHECK (side IN ('debit','credit')),
    amount      TEXT    NOT NULL,
    manual_override INTEGER NOT NULL DEFAULT 0,
    UNIQUE (entry_id, account_id, side)
);

CREATE TABLE IF NOT EXISTS categorization_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword         TEXT    NOT NULL,
    account_id      INTEGER NOT NULL REFERENCES accounts(id),
    priority        INTEGER NOT NULL DEFAULT 100,
    match_mode      TEXT    NOT NULL DEFAULT 'contains' CHECK (match_mode IN ('contains','equals')),
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rules_account ON categorization_rules(account_id);

CREATE TABLE IF NOT EXISTS statements (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    imported_at  TEXT    NOT NULL,
    source_name  TEXT,
    statement_json TEXT  NOT NULL
);

CREATE TABLE IF NOT EXISTS vendors (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE,
    pan        TEXT,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_type        TEXT    NOT NULL,
    reference       TEXT,
    vendor_id       INTEGER REFERENCES vendors(id),
    memo            TEXT    NOT NULL,
    amount          TEXT    NOT NULL,
    entry_date      TEXT    NOT NULL,
    confidence      TEXT    NOT NULL,
    status          TEXT    NOT NULL CHECK (status IN ('posted','exception','queued')),
    source          TEXT,
    posted_entry_id INTEGER REFERENCES journal_entries(id),
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor       TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    entity      TEXT    NOT NULL,
    entity_id   TEXT,
    detail      TEXT,
    created_at  TEXT    NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a fresh SQLite connection with the desired row factory.

    ``db_path`` defaults to the configured DB location. ``check_same_thread``
    is disabled so the same pool can serve concurrent request threads.
    """
    path = Path(db_path or config.DB_PATH).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        detect_types=sqlite3.PARSE_DECLTYPES,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables (and indexes) if they do not yet exist."""
    conn.executescript(_SCHEMA_STATEMENTS)
    conn.commit()


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

def audit(
    conn: sqlite3.Connection,
    actor: str,
    action: str,
    entity: str,
    entity_id: str | None = None,
    detail: str | None = None,
) -> None:
    """Append an immutable audit record. Internally wrapped by mutators, but
    exposed so endpoints may log bespoke events (imports, reconciliations)."""
    created_at = now_iso()
    conn.execute(
        "INSERT INTO audit_log (actor, action, entity, entity_id, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (actor, action, entity, str(entity_id) if entity_id is not None else None, detail, created_at),
    )


# ---------------------------------------------------------------------------
# Seed data (sample chart of accounts + a couple of posted entries)
# ---------------------------------------------------------------------------

_CHART = [
    ("1000", "Cash", "asset", "debit"),
    ("1100", "Bank Account", "asset", "debit"),
    ("1200", "Accounts Receivable", "asset", "debit"),
    ("2000", "Accounts Payable", "liability", "credit"),
    ("2100", "Short-term Loan", "liability", "credit"),
    ("3000", "Owner's Equity", "equity", "credit"),
    ("3100", "Retained Earnings", "equity", "credit"),
    ("4000", "Sales Revenue", "income", "credit"),
    ("4100", "Service Revenue", "income", "credit"),
    ("4300", "Interest Income", "income", "credit"),
    ("5000", "Cost of Goods Sold", "expense", "debit"),
    ("5100", "Salaries & Wages", "expense", "debit"),
    ("5200", "Rent Expense", "expense", "debit"),
    ("5300", "Utilities", "expense", "debit"),
    ("5400", "Travel & Meals", "expense", "debit"),
    ("5500", "Software & Subscriptions", "expense", "debit"),
    ("5600", "Marketing & Advertising", "expense", "debit"),
]

_RULES = [
    ("SALARY", "5100", 100), ("WAGES", "5100", 100), ("PAYROLL", "5100", 100),
    ("RENT", "5200", 100),
    ("ELECTRICITY", "5300", 100), ("UTILITIES", "5300", 100), ("WATER", "5300", 100), ("GAS", "5300", 100),
    ("TRAVEL", "5400", 100), ("FLIGHT", "5400", 100), ("AIRLINE", "5400", 100), ("HOTEL", "5400", 100),
    ("SOFTWARE", "5500", 120), ("SUBSCRIPTION", "5500", 120),
    ("AZURE", "5500", 120), ("GCP", "5500", 120), ("AWS", "5500", 120), ("STRIPE", "5500", 120),
    ("ADOBE", "5500", 120), ("NETFLIX", "5500", 120),
    ("GOOGLE ADS", "5600", 110), ("FACEBOOK", "5600", 110), ("ADS", "5600", 110), ("MARKETING", "5600", 110),
    ("INTEREST", "4300", 100),
    ("INVOICE", "4000", 100), ("SALE", "4000", 100),
]

_SEED_ENTRIES = [
    # Owner injects capital.
    {
        "memo": "Owner capital injection",
        "entry_date": "2026-01-05",
        "lines": [
            ("1100", "debit", "50000.00"),
            ("3000", "credit", "50000.00"),
        ],
    },
    # Rent paid from Bank.
    {
        "memo": "January office rent",
        "entry_date": "2026-01-06",
        "lines": [
            ("5200", "debit", "2500.00"),
            ("1100", "credit", "2500.00"),
        ],
    },
    # A service invoice.
    {
        "memo": "Consulting revenue",
        "entry_date": "2026-01-10",
        "lines": [
            ("1200", "debit", "3200.00"),
            ("4100", "credit", "3200.00"),
        ],
    },
]


def seed(conn: sqlite3.Connection) -> None:
    """Insert idempotent seed rows: chart of accounts, categorisation rules and
    a handful of posted journal entries (memo-checked so re-runs are safe)."""
    created_at = now_iso()

    if conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0:
        for code, name, atype, norm in _CHART:
            conn.execute(
                "INSERT INTO accounts (code, name, type, normal_balance, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 1, ?, ?)",
                (code, name, atype, norm, created_at, created_at),
            )
        conn.commit()
        audit(conn, "system", "bulk_seed", "accounts", None, "Seeded default chart of accounts")

    if conn.execute("SELECT COUNT(*) FROM categorization_rules").fetchone()[0] == 0:
        for keyword, code, prio in _RULES:
            account_id = conn.execute("SELECT id FROM accounts WHERE code = ?", (code,)).fetchone()
            if account_id is None:
                continue
            conn.execute(
                "INSERT INTO categorization_rules (keyword, account_id, priority, is_active, created_at) "
                "VALUES (?, ?, ?, 1, ?)",
                (keyword, account_id[0], prio, created_at),
            )
        conn.commit()
        audit(conn, "system", "bulk_seed", "categorization_rules", None, "Seeded default rules")

    if conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0] == 0:
        for name, pan in [("Acme Supplies", "ACMPA1234A"), ("Vertex Solutions", "VTXPA5678B")]:
            conn.execute(
                "INSERT INTO vendors (name, pan, created_at) VALUES (?, ?, ?)",
                (name, pan, created_at),
            )
        conn.commit()
        audit(conn, "system", "bulk_seed", "vendors", None, "Seeded default vendor master")

    if conn.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0] == 0:
        for seed_entry in _SEED_ENTRIES:
            code_to_id = {
                row["code"]: row["id"]
                for row in conn.execute("SELECT id, code FROM accounts").fetchall()
            }
            entry_cursor = conn.execute(
                "INSERT INTO journal_entries (entry_no, memo, entry_date, status, created_by, created_at, posted_at) "
                "VALUES (?, ?, ?, 'posted', 'seed', ?, ?)",
                (f"JE-{created_at[:10]}-{seed_entry['memo']}", seed_entry["memo"],
                 seed_entry["entry_date"], created_at, created_at),
            )
            entry_id = entry_cursor.lastrowid
            for account_code, side, amount in seed_entry["lines"]:
                conn.execute(
                    "INSERT INTO journal_lines (entry_id, account_id, side, amount, manual_override) "
                    "VALUES (?, ?, ?, ?, 0)",
                    (entry_id, code_to_id[account_code], side, amount),
                )
        conn.commit()
        audit(conn, "system", "bulk_seed", "journal_entries", None, "Seeded sample journal entries")


def ensure_schema_and_seed(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Idempotently create the schema and seed data, returning a connection."""
    conn = connect(db_path)
    init_schema(conn)
    seed(conn)
    return conn


def connect_app() -> sqlite3.Connection:
    """Convenience used by the FastAPI dependency to boot the DB once."""
    conn = ensure_schema_and_seed()
    return conn


# ---------------------------------------------------------------------------
# Generic scalar helpers
# ---------------------------------------------------------------------------

def fetch_id_by(conn: sqlite3.Connection, table: str, **criteria) -> Any | None:
    """Return the id column for the first row satisfying all criteria."""
    if not criteria:
        raise ValueError("criteria required")
    clause = " AND ".join(f"{k} = ?" for k in criteria)
    row = conn.execute(
        f"SELECT id FROM {table} WHERE {clause} LIMIT 1",
        tuple(criteria.values()),
    ).fetchone()
    return row["id"] if row else None


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    """Convert a sequence of sqlite3.Row into plain dicts."""
    return [dict(r) for r in rows]