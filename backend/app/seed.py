"""Standalone seed / reset module.

The FastAPI app seeds its database automatically on first boot (via
:func:`app.db.ensure_schema_and_seed`). This module exists so operators can
seed — or completely reset — the database without starting the server::

    python -m app.seed            # seed an existing/empty DB (idempotent)
    python -m app.seed --reset    # delete the DB file, then reseed fresh
    python -m app.seed --path x.db # seed a specific database file

Seeding is idempotent: existing rows are never duplicated.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import config, db

__all__ = ["seed_database", "reset_database", "main"]


def seed_database(db_path: Path | str | None = None) -> Path:
    """Open (or create) a database at ``db_path`` and seed schema + data.

    Returns the resolved database path.
    """
    conn = db.ensure_schema_and_seed(db_path)
    conn.close()
    return Path(db_path or config.DB_PATH).resolve()


def reset_database(db_path: Path | str | None = None, *, keep_backup: bool = True) -> Path:
    """Delete the current database (and WAL/SHM files), then reseed fresh."""
    path = Path(db_path or config.DB_PATH).resolve()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    for suffix in ("-wal", "-shm"):
        try:
            Path(str(path) + suffix).unlink()
        except FileNotFoundError:
            pass
    return seed_database(path)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LedgerMind database seed/reset")
    parser.add_argument(
        "--reset", action="store_true",
        help="delete the database file then reseed it fresh",
    )
    parser.add_argument(
        "--path", default=str(config.DB_PATH),
        help="database file to seed (default: configured LEDGERMIND_DB_PATH)",
    )
    args = parser.parse_args(argv)

    path = Path(args.path)
    if args.reset:
        resolved = reset_database(path)
        print(f"[seed] reset + reseeded  -> {resolved}")
    else:
        resolved = seed_database(path)
        print(f"[seed] database ready     -> {resolved}")

    # Report a summary of what the seeded DB actually contains.
    conn = db.connect(resolved)
    for table in ("accounts", "categorization_rules", "vendors",
                  "journal_entries", "journal_lines", "documents"):
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"[seed] {table:<22} {n}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(_main())


def main() -> None:  # convenience for tooling
    _main()