# Deployment

*Version: v0.1 · 15-11-26 · Author: SHIVAM ITCS (JARVIS)*

LedgerMind is a self-contained FastAPI service with a single SQLite file for
storage — there is no external database, cache or ML dependency. This makes it
trivial to deploy to almost anything that can run a Python process.

## Build the deployment artifact

Create a fresh, reproducible environment:

```bash
cd backend
python -m venv .venv-prod
.venv-prod/Scripts/python -m pip install -r requirements.txt
```

No build step is needed — the app is pure Python. Optionally freeze the
exact trees for reproducibility:

```bash
.venv-prod/Scripts/python -m pip freeze > requirements-lock.txt
```

## Run the production server

Uvicorn is already present (`uvicorn[standard]`). Run it with multiple worker
processes for throughput:

```bash
cd backend
LEDGERMIND_DB_PATH=/var/lib/ledgermind/app.db \
.venv-prod/Scripts/python -m uvicorn app.main:app \
  --host 0.0.0.0 --port 8000 --workers 4
```

> SQLite note: with the default connection settings WAL is enabled, allowing
> concurrent readers and a single writer. Prefer a small worker count
> (1–4) over many; the `check_same_thread=False` setting lets each connection
> serve concurrent request threads.

### Reverse proxy

Put uvicorn behind nginx/Caddy. A minimal nginx block:

```nginx
server {
    listen 80;
    server_name ledgermind.example.com;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-User $http_x_user;   # optional auth passthrough
    }
}
```

Terminate TLS at the proxy; the app itself is plain HTTP.

## Environment configuration

See [`env-config.md`](env-config.md) for the full variable reference (template:
`.env.example`). In production the
two settings to care about are:

| Variable | Production recommendation |
|---|---|
| `LEDGERMIND_DB_PATH` | An absolute path inside a persistent, backed-up volume (e.g. `/var/lib/ledgermind/app.db`). Make the parent dir writable by the app user — the app creates it if missing. |
| `LEDGERMIND_CONFIDENCE_THRESHOLD` | Tune the straight-through rate. Lower = more auto-posts (fewer exceptions but more trust in rules); higher = safer default that routes borderline documents to humans. |

## Backups

The **whole database is one file** (+ `-wal`/`-shm` during writes), so backups
are trivial. Use SQLite's online backup API for a consistent snapshot rather
than copying the live file:

```bash
cd backend
.venv-prod/Scripts/python - c "
import sqlite3
src = sqlite3.connect('data/ledgermind.db')
dst = sqlite3.connect('data/ledgermind-backup-$(date +%F).db')
src.backup(dst); dst.close(); src.close()
"
```

Restore = copy the file back (stop the server first).

## Health check & observability

- **Liveness/readiness:** `GET /health` returns `200 {"status":"ok","version":...}`.
- **App boot:** the lifespan hook logs a summary line
  (`LedgerMind 1.0.0 ready @ ... accounts=17 ...`) on every start — a good
  "did it seed correctly?" signal for log scraping.
- **Audit:** every mutation is already in the append-only `audit_log` table —
  for compliance it is the system of record; consider a job that mirrors it to
  a read-only copy.

## Deploying with systemd (Linux example)

```ini
# /etc/systemd/system/ledgermind.service
[Unit]
Description=LedgerMind API
After=network.target

[Service]
WorkingDirectory=/opt/ledgermind/backend
Environment=LEDGERMIND_DB_PATH=/var/lib/ledgermind/app.db
ExecStart=/opt/ledgermind/backend/.venv-prod/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 2
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now ledgermind
```

## Container (optional)

```dockerfile
FROM python:3.14-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV LEDGERMIND_DB_PATH=/data/ledgermind.db
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Mount `/data` on a persistent volume for the SQLite database.

## Security considerations

- The API currently exposes **no authentication** — the audit `actor` is
  whatever `X-User` a client sends (defaulting to `system`). If this service
  is exposed beyond a trusted network, put authentication in front of it (e.g.
  mTLS at the proxy, or an auth gateway that sets `X-User` from the verified
  identity). This is a documented limitation of `v1.0.0`, not a design
  guarantee.
- Do not delete rows from `audit_log`; it is the compliance backstop.