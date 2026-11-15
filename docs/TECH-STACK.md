# Technology Stack

*Version: v0.1 · 15-11-26 · Author: SHIVAM ITCS (JARVIS)*

Versions below are the exact versions pinned in the active virtual
environment (verified `2026-11-13`). `requirements.txt` lists only the direct
dependencies; everything else is pulled transitively by pip.

## Runtime

| Component | Version | Notes |
|---|---|---|
| **Python** | 3.14.9 | Developed and verified on this version; code targets 3.10+ (`X \| None` type syntax needs ≥3.10) |
| **FastAPI** | 0.162.5 | Web framework; automatic OpenAPI docs at `/docs` |
| **Pydantic** | 2.14.3 | v2 — validation + serialisation; all API contracts |
| **Uvicorn** | 0.54.0 | ASGI server (`uvicorn app.main:app`) |
| **httpx** | 0.28.1 | async HTTP client (FastAPI `TestClient` uses it) |
| **pytest** | latest | Test runner |

## Storage

| Component | Version | Notes |
|---|---|---|
| **SQLite** | stdlib (`sqlite3`) | Zero-config embedding; no server. File: `backend/data/ledgermind.db` by default |
| SQLite mode | WAL | `PRAGMA journal_mode = WAL` set per-connection for concurrent readers |
| Foreign keys | ON | `PRAGMA foreign_keys = ON` enforced per-connection |

Money is stored as **TEXT** (not `REAL`) — see
[ARCHITECTURE.md](ARCHITECTURE.md#money-as-text) for the rationale.

## Dependencies (`backend/requirements.txt`)

```
fastapi
uvicorn[standard]
pytest
httpx
pydantic
```

These are the only direct dependencies. There are **no** heavyweight runtime
dependencies: no ORM, no async DB driver, no ML service. The "AI" in the
document pipeline is implemented in-process as deterministic heuristics
(categorisation rules + confidence scoring) so the whole product runs offline
with nothing but the standard library + the five panel deps.

## Environment configuration

All configuration is environment-variable driven with sensible defaults
(see `app/config.py`):

| Variable | Default | Purpose |
|---|---|---|
| `LEDGERMIND_DB_PATH` | `backend/data/ledgermind.db` | SQLite database file (absolute or relative) |
| `LEDGERMIND_CONFIDENCE_THRESHOLD` | `0.80` | Auto-post confidence gate (0..1) |
| `LEDGERMIND_RECON_WINDOW` | `3` | Statement-reconciliation match window (days) |
| `LEDGERMIND_DEFAULT_USER` | `system` | Actor stamped in the audit log when a request carries no `X-User` header |

## Version-notes / gotchas

- Python 3.14 emits no deprecation issues on this stack, but the FastAPI
  `TestClient` currently prints a `StarletteDeprecationWarning` about `httpx`
  (harmless; `httpx2` support is a future upstream change).
- The codebase uses Pydantic v2 idioms only (`model_validator`,
  `model_dump`, `field_validator`). It is **not** compatible with Pydantic v1.