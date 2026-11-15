"""Data-access layer: thin, explicit SQL over :mod:`app.db`.

Every business entity (accounts, journal entries, rules, documents, vendors,
statements, audit records) gets its own module-level functions here. They
take a ``sqlite3.Connection`` and return plain dicts / lists of dicts so the
HTTP layer (routers) stays free of SQL. Money stays as ``str`` down in the DB
and is converted to ``Decimal`` only at the API boundary in the schemas.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from . import config, db

# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #

def create_account(conn: sqlite3.Connection, *, code: str, name: str,
                   atype: str, normal_balance: str, is_active: bool,
                   actor: str) -> dict[str, Any]:
    now = db.now_iso()
    cur = conn.execute(
        "INSERT INTO accounts (code, name, type, normal_balance, is_active, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (code, name, atype, normal_balance, int(is_active), now, now),
    )
    conn.commit()
    db.audit(conn, actor, "create", "accounts", cur.lastrowid, f"code={code} name={name}")
    return get_account(conn, cur.lastrowid)  # type: ignore[return-value]


def get_account(conn: sqlite3.Connection, account_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM accounts a WHERE a.id = ?", (account_id,)
    ).fetchone()
    return dict(row) if row else None


def list_accounts(conn: sqlite3.Connection, active_only: bool = True) -> list[dict[str, Any]]:
    sql = "SELECT * FROM accounts"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY code"
    return db.rows_to_dicts(conn.execute(sql).fetchall())


def balance_of(conn: sqlite3.Connection, account_id: int) -> Decimal:
    """Net balance of an account as of the posted ledger (debits minus credits).

    Uses the account's ``normal_balance`` so a positive return means the
    account sits on its normal side.
    """
    row = conn.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN l.side = 'debit' THEN CAST(l.amount AS REAL)
                                ELSE -CAST(l.amount AS REAL) END), 0) AS net
          FROM journal_lines l
          JOIN journal_entries e ON e.id = l.entry_id
         WHERE l.account_id = ? AND e.status = 'posted'
        """,
        (account_id,),
    ).fetchone()
    net = Decimal(str(row["net"]))
    account = get_account(conn, account_id) or {}
    if account.get("normal_balance") == "credit":
        net = -net
    return net.quantize(Decimal("0.01"))


def ledger(conn: sqlite3.Connection, account_id: int) -> list[dict[str, Any]]:
    """Posted journal lines touching an account, newest first."""
    return db.rows_to_dicts(conn.execute(
        """
        SELECT l.id, l.side, l.amount, e.id AS entry_id, e.entry_no, e.memo,
               e.entry_date, e.posted_at
          FROM journal_lines l
          JOIN journal_entries e ON e.id = l.entry_id
         WHERE l.account_id = ? AND e.status = 'posted'
         ORDER BY e.entry_date DESC, l.id DESC
        """,
        (account_id,),
    ).fetchall())


# --------------------------------------------------------------------------- #
# Journal entries
# --------------------------------------------------------------------------- #

def next_entry_no(conn: sqlite3.Connection, entry_date: date) -> str:
    """Human-friendly sequential entry number: ``JE-YYYYMMDD-NNN``."""
    prefix = f"JE-{entry_date.strftime('%Y%m%d')}"
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM journal_entries WHERE entry_no LIKE ?",
        (prefix + "%",),
    ).fetchone()
    return f"{prefix}-{row['n'] + 1:03d}"


def create_journal_entry(
    conn: sqlite3.Connection,
    *,
    memo: str,
    entry_date: date,
    lines: Iterable[tuple[int, str, str]],
    status: str = "posted",
    actor: str = "system",
    entry_no: str | None = None,
) -> dict[str, Any] | None:
    """Insert a journal entry and its lines in one transaction.

    ``lines`` is an iterable of ``(account_id, side, amount_str)`` tuples. The
    double-entry balance invariant is enforced at the schema layer
    (see :class:`app.schemas.JournalEntryCreate`), but we re-check it here too
    so direct / test callers cannot post an unbalanced entry.
    """
    total = sum(Decimal(a) if s == "debit" else -Decimal(a) for _, s, a in lines)
    if total != 0:
        raise ValueError("journal entry must balance (debits == credits)")

    now = db.now_iso()
    number = entry_no or next_entry_no(conn, entry_date)
    cur = conn.execute(
        "INSERT INTO journal_entries (entry_no, memo, entry_date, status, created_by, created_at, posted_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (number, memo, entry_date.isoformat(), status, actor, now, now if status == "posted" else None),
    )
    entry_id = cur.lastrowid
    for account_id, side, amount in lines:
        conn.execute(
            "INSERT INTO journal_lines (entry_id, account_id, side, amount, manual_override) "
            "VALUES (?, ?, ?, ?, 0)",
            (entry_id, account_id, side, str(amount)),
        )
    conn.commit()
    db.audit(conn, actor, "create", "journal_entries", entry_id, f"memo={memo} status={status}")
    return get_journal_entry(conn, entry_id)


def get_journal_entry(conn: sqlite3.Connection, entry_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM journal_entries WHERE id = ?", (entry_id,)).fetchone()
    if not row:
        return None
    entry = dict(row)
    entry["lines"] = db.rows_to_dicts(conn.execute(
        """
        SELECT l.*, a.code AS account_code, a.name AS account_name
          FROM journal_lines l JOIN accounts a ON a.id = l.account_id
         WHERE l.entry_id = ?
         ORDER BY l.id
        """,
        (entry_id,),
    ).fetchall())
    return entry


def list_journal_entries(conn: sqlite3.Connection, status: str | None = None,
                         limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
    sql = "SELECT * FROM journal_entries"
    args: list[Any] = []
    if status:
        sql += " WHERE status = ?"
        args.append(status)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    return db.rows_to_dicts(conn.execute(sql, args).fetchall())


def post_journal_entry(conn: sqlite3.Connection, entry_id: int, actor: str) -> dict[str, Any] | None:
    """Flip a draft entry to posted (idempotent on already-posted entries)."""
    entry = get_journal_entry(conn, entry_id)
    if not entry:
        return None
    if entry["status"] == "draft":
        conn.execute(
            "UPDATE journal_entries SET status = 'posted', posted_at = ? WHERE id = ?",
            (db.now_iso(), entry_id),
        )
        conn.commit()
        db.audit(conn, actor, "post", "journal_entries", entry_id, entry["entry_no"])
    return get_journal_entry(conn, entry_id)


# --------------------------------------------------------------------------- #
# Categorisation rules
# --------------------------------------------------------------------------- #

def create_rule(conn: sqlite3.Connection, *, keyword: str, account_code: str,
                priority: int, match_mode: str, actor: str) -> dict[str, Any] | None:
    account = conn.execute("SELECT id FROM accounts WHERE code = ?", (account_code,)).fetchone()
    if not account:
        raise ValueError(f"unknown account code: {account_code}")
    cur = conn.execute(
        "INSERT INTO categorization_rules (keyword, account_id, priority, match_mode, is_active, created_at) "
        "VALUES (?, ?, ?, ?, 1, ?)",
        (keyword, account["id"], priority, match_mode, db.now_iso()),
    )
    conn.commit()
    db.audit(conn, actor, "create", "categorization_rules", cur.lastrowid, keyword)
    row = conn.execute(
        """
        SELECT r.*, a.code AS account_code
          FROM categorization_rules r JOIN accounts a ON a.id = r.account_id
         WHERE r.id = ?
        """,
        (cur.lastrowid,),
    ).fetchone()
    return dict(row) if row else None


def list_rules(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return db.rows_to_dicts(conn.execute(
        """
        SELECT r.id, r.keyword, r.priority, r.match_mode, r.is_active, r.created_at,
               a.id AS account_id, a.code AS account_code, a.name AS account_name
          FROM categorization_rules r JOIN accounts a ON a.id = r.account_id
         WHERE r.is_active = 1
         ORDER BY r.priority ASC
        """
    ).fetchall())


def match_rule(conn: sqlite3.Connection, memo: str) -> dict[str, Any] | None:
    """Find the best categorisation rule for a transaction memo.

    Rules are ranked by ascending ``priority`` (lower number wins); within the
    same priority the earliest matching rule is chosen. ``contains`` matches a
    case-insensitive substring, ``equals`` requires an exact (case-folded)
    match.
    """
    folded = memo.casefold()
    for rule in list_rules(conn):
        keyword = rule["keyword"].casefold()
        if rule["match_mode"] == "equals":
            hit = folded == keyword
        else:
            hit = keyword in folded
        if hit:
            return rule
    return None


# --------------------------------------------------------------------------- #
# Vendors
# --------------------------------------------------------------------------- #

def find_vendor(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM vendors WHERE name = ? COLLATE NOCASE LIMIT 1", (name,)
    ).fetchone()
    return dict(row) if row else None


def create_vendor(conn: sqlite3.Connection, *, name: str, pan: str | None,
                  actor: str) -> dict[str, Any]:
    cur = conn.execute(
        "INSERT INTO vendors (name, pan, created_at) VALUES (?, ?, ?)",
        (name, pan, db.now_iso()),
    )
    conn.commit()
    db.audit(conn, actor, "create", "vendors", cur.lastrowid, name)
    return dict(conn.execute("SELECT * FROM vendors WHERE id = ?", (cur.lastrowid,)).fetchone())


def list_vendors(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return db.rows_to_dicts(conn.execute("SELECT * FROM vendors ORDER BY name").fetchall())


# --------------------------------------------------------------------------- #
# Documents (pipeline persistence)
# --------------------------------------------------------------------------- #

def save_document(conn: sqlite3.Connection, *, doc_type: str, memo: str,
                  amount: str, entry_date: str, confidence: str, status: str,
                  vendor_id: int | None, reference: str | None, source: str | None,
                  posted_entry_id: int | None) -> dict[str, Any] | None:
    cur = conn.execute(
        "INSERT INTO documents (doc_type, reference, vendor_id, memo, amount, entry_date, confidence, status, source, posted_entry_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (doc_type, reference, vendor_id, memo, str(amount), entry_date, str(confidence),
         status, source, posted_entry_id, db.now_iso()),
    )
    conn.commit()
    return get_document(conn, cur.lastrowid)


def get_document(conn: sqlite3.Connection, document_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT d.*, v.name AS vendor
          FROM documents d LEFT JOIN vendors v ON v.id = d.vendor_id
         WHERE d.id = ?
        """,
        (document_id,),
    ).fetchone()
    return dict(row) if row else None


def list_documents(conn: sqlite3.Connection, status: str | None = None,
                   limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
    sql = """SELECT d.*, v.name AS vendor
               FROM documents d LEFT JOIN vendors v ON v.id = d.vendor_id"""
    args: list[Any] = []
    if status:
        sql += " WHERE d.status = ?"
        args.append(status)
    sql += " ORDER BY d.id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    return db.rows_to_dicts(conn.execute(sql, args).fetchall())


# --------------------------------------------------------------------------- #
# Statements & reconciliation
# --------------------------------------------------------------------------- #

def store_statement(conn: sqlite3.Connection, *, source_name: str | None,
                    lines: list[dict[str, Any]], actor: str) -> dict[str, Any]:
    cur = conn.execute(
        "INSERT INTO statements (imported_at, source_name, statement_json) VALUES (?, ?, ?)",
        (db.now_iso(), source_name, json.dumps(lines, default=str)),
    )
    conn.commit()
    db.audit(conn, actor, "import", "statements", cur.lastrowid,
             f"{len(lines)} lines from {source_name or 'unknown source'}")
    return dict(conn.execute(
        "SELECT id, imported_at, source_name, statement_json FROM statements WHERE id = ?",
        (cur.lastrowid,),
    ).fetchone())


def list_statements(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    return db.rows_to_dicts(conn.execute(
        "SELECT id, imported_at, source_name FROM statements ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall())


def reconcile_statement(conn: sqlite3.Connection, stored_id: int,
                        window_days: int | None = None) -> list[dict[str, Any]]:
    """Match a stored statement's lines against posted journal lines.

    A line matches when a *posted* journal line exists on a compatible
    ``entry_date`` (within ``window_days``) with the same absolute amount and
    a non-empty overlapping memo. Matched entries are returned so the caller
    can prove bank <> ledger agreement.

    ``window_days`` defaults to the configured reconciliation window.
    """
    row = conn.execute(
        "SELECT statement_json FROM statements WHERE id = ?", (stored_id,)
    ).fetchone()
    if not row:
        raise LookupError(f"no statement with id {stored_id}")
    window = window_days or config.RECONCILIATION_WINDOW_DAYS
    lines = json.loads(row["statement_json"])

    posted = conn.execute(
        """
        SELECT l.id AS line_id, l.amount, l.side, e.id AS entry_id, e.entry_no,
               e.entry_date, e.memo
          FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id
         WHERE e.status = 'posted'
        """
    ).fetchall()

    results: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        amt = abs(Decimal(str(line["amount"])))
        date_ok = lambda d: abs((d - date.fromisoformat(line["posted_on"])).days) <= window  # noqa: E731
        best = None
        for pl in posted:
            if Decimal(str(pl["amount"])) != amt:
                continue
            if not date_ok(date.fromisoformat(pl["entry_date"])):
                continue
            if best is None or _memo_score(pl["memo"], line["memo"]) > _memo_score(best["memo"], line["memo"]):
                best = pl
        if best:
            account = conn.execute(
                "SELECT code FROM accounts a JOIN journal_lines l ON l.account_id = a.id WHERE l.id = ?",
                (best["line_id"],),
            ).fetchone()
            results.append({
                "line_index": idx, "memo": line["memo"], "amount": str(amt),
                "matched": True, "entry_id": best["entry_id"], "entry_no": best["entry_no"],
                "account_code": account["code"] if account else None,
                "reason": f"matched posted journal entry within {window}-day window",
            })
        else:
            results.append({
                "line_index": idx, "memo": line["memo"], "amount": str(amt),
                "matched": False, "entry_id": None, "entry_no": None,
                "account_code": None, "reason": "no matching posted entry",
            })
    return results


def _memo_score(journal_memo: str, line_memo: str) -> int:
    """0..3 similarity used to disambiguate same-amount candidates."""
    j, l = journal_memo.casefold(), line_memo.casefold()
    score = 0
    if j == l:
        score += 3
    if l in j or j in l:
        score += 2
    words = set(l.split()) & set(j.split())
    score += min(len(words), 1)
    return score


# --------------------------------------------------------------------------- #
# Audit trail
# --------------------------------------------------------------------------- #

def list_audit(conn: sqlite3.Connection, entity: str | None = None,
               limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
    sql = "SELECT * FROM audit_log"
    args: list[Any] = []
    if entity:
        sql += " WHERE entity = ?"
        args.append(entity)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    return db.rows_to_dicts(conn.execute(sql, args).fetchall())