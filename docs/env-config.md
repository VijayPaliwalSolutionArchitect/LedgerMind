# LedgerMind — Environment Configuration Reference

*Version: v0.1 · 15-11-26 · Author: SHIVAM ITCS (JARVIS)*

Every deployable knob for LedgerMind, read from the **environment at import
time** by `backend/app/config.py`. The app has **no dotenv loader** — export
the variables in your shell, or set them in systemd/Docker/Coolify, **before**
the app process starts (config values are frozen at import). `.env.example`
at the repo root is the committed template (placeholders only; `.env` is
gitignored).

**Status legend**

- **optional** — has a safe default; override only when needed.
- **live** — consumed by the running code today.

---

## 1. Core runtime configuration (`app/config.py`)

| Variable | Purpose | Default | Status |
|----------|---------|---------|--------|
| `LEDGERMIND_DB_PATH` | SQLite database file (absolute or relative). Parent dir is auto-created; schema + seed run on first boot. | `backend/data/ledgermind.db` | optional (live) |
| `LEDGERMIND_CONFIDENCE_THRESHOLD` | Auto-post confidence gate for the document pipeline (0..1). Lower = more straight-through posting; higher = more exceptions routed to humans. | `0.80` | optional (live) |
| `LEDGERMIND_RECON_WINDOW` | Bank-statement reconciliation match window in days (± around posted date). | `3` | optional (live) |
| `LEDGERMIND_DEFAULT_USER` | Audit actor when a request carries no `X-User` header. | `system` | optional (live) |

## 2. Per-request header (not an env var)

| Header | Purpose | Default | Read by |
|--------|---------|---------|---------|
| `X-User: <name>` | Stamps the acting user into `audit_log` rows for that request. | `LEDGERMIND_DEFAULT_USER` | `routers/deps.py::get_audit` |

## 3. Introspection

`GET /api/v1/system/config` returns the effective runtime values:

```json
{
  "db_path": ".../backend/data/ledgermind.db",
  "reconciliation_window_days": 3,
  "confidence_threshold": "0.80",
  "default_user": "system"
}
```

## 4. Server bind (uvicorn CLI flags, not env vars)

| Flag | Purpose | Default |
|------|---------|---------|
| `--host` | Bind address | `0.0.0.0` via `python -m app.main`; `127.0.0.1` via uvicorn CLI |
| `--port` | Port | `8000` |
| `--workers` | Process count | `1` (keep small — SQLite single-writer; see DEPLOYMENT.md) |
| `--reload` | Dev auto-reload | off |

## 5. Example shells

```bash
# Linux/macOS
export LEDGERMIND_DB_PATH=/var/lib/ledgermind/app.db
export LEDGERMIND_CONFIDENCE_THRESHOLD=0.90
cd backend && uvicorn app.main:app --host 127.0.0.1 --port 8000

# Windows (PowerShell)
$env:LEDGERMIND_DB_PATH="C:\data\ledgermind.db"
cd backend; .venv/Scripts/python -m uvicorn app.main:app --port 8000

# Docker / Compose
environment:
  LEDGERMIND_DB_PATH: /data/ledgermind.db
  LEDGERMIND_CONFIDENCE_THRESHOLD: "0.80"
```

---

## Notes

- **No secrets exist in this project.** No API keys, tokens, or passwords are
  read or required — the document pipeline is deterministic and offline. Never
  add real credentials to `.env.example`; use `[REDACTED]`/placeholders only.
- The DB file (`backend/data/`, `*.db`, `*.db-wal`, `*.db-shm`) is gitignored —
  never commit data.
- Because config is read at **import time**, tests set `LEDGERMIND_DB_PATH` to
  a temp file *before* importing `app` (see `backend/tests/conftest.py`).
- If code and this doc ever disagree, the code wins: `backend/app/config.py`
  is the source of truth.
