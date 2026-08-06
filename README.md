# RCON Server Manager

Docker-friendly web app for **remote game server stats & control** via **Source Query** and **Source RCON**.

**Insurgency: Sandstorm** is the first supported server type. The architecture is pluggable so additional games can be added as types later.

**Repository:** [SawPsyder/rcon_server_manager](https://github.com/SawPsyder/rcon_server_manager)

## Stack

| Piece | Role |
|--------|------|
| **rcon-manager** | FastAPI + React SPA (one image) |
| **db** | PostgreSQL 16 (separate container, named volume) |

Local development without Docker can still use **SQLite** (default when `DATABASE_URL` / `POSTGRES_HOST` are unset).

## Features

- Single-admin authentication (session cookie)
- Multi-server CRUD with **server types** and encrypted RCON passwords
- Live server status (A2S query) + auto-refresh
- Continuous player-count sampling + history chart
- Type-aware player list, kick/ban, map travel (Sandstorm)
- **Persistent RCON connections** (avoids Sandstorm per-connect thread leak)
- RCON console with per-type command allowlist
- Hybrid config: type defaults + optional per-server overrides

## Branches & releases

| Branch / event | Purpose |
|----------------|---------|
| `develop` | Day-to-day work |
| `master` | Stable / release line |
| Tag `vX.Y.Z` on `master` | CI builds and publishes Docker image to GHCR |

### Publish a release image

```bash
git checkout master
git merge develop   # when ready
git push origin master
git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0
```

The [Docker release workflow](.github/workflows/docker-release.yml) only publishes if the tag points at a commit on `master`.

### Pull a published image

```bash
docker pull ghcr.io/sawpsyder/rcon_server_manager:latest
# or a version:
docker pull ghcr.io/sawpsyder/rcon_server_manager:0.1.0
```

(Package visibility may need to be set under GitHub → Packages if the repo is private.)

## Quick start (Docker Compose + Postgres)

```bash
cp .env.example .env
# set ADMIN_PASSWORD, SECRET_KEY, ENCRYPTION_KEY, POSTGRES_PASSWORD

docker compose up -d --build
```

Open **http://localhost:8080** and sign in with `ADMIN_PASSWORD`.

Services:

- `db` — Postgres 16, healthchecked, data in volume `rcon_manager_pgdata`
- `rcon-manager` — app; waits until Postgres is healthy; RCON connection pool enabled

### Environment

| Variable | Description |
|----------|-------------|
| `ADMIN_PASSWORD` | Initial admin password (hashed on first boot) |
| `SECRET_KEY` | Session cookie signing secret |
| `ENCRYPTION_KEY` | Fernet key for RCON passwords at rest |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Database credentials |
| `DATABASE_URL` | Optional full SQLAlchemy URL (Compose builds from `POSTGRES_*` when unset) |
| `DATA_DIR` | App data dir for encryption key file (default `/data`; **not** the SQL store in Compose) |
| `SESSION_HTTPS_ONLY` | Set `true` behind HTTPS reverse proxy |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | SQLAlchemy pool tuning |

Generate a Fernet key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Local development

### Backend with SQLite (simplest)

```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
set ADMIN_PASSWORD=admin
set SECRET_KEY=dev
set DATA_DIR=../data
# leave DATABASE_URL unset → sqlite under DATA_DIR/app.db
uvicorn app.main:app --reload --port 8080
```

### Backend with Postgres (e.g. only `db` from Compose)

```bash
docker compose up -d db
set DATABASE_URL=postgresql+psycopg://rcon:rcon@127.0.0.1:5432/rcon_manager
uvicorn app.main:app --reload --port 8080
```

### Frontend

```bash
cd frontend
npm install
npm run build
# copies dist → backend/app/static (gitignored; Docker builds this itself)
```

## Migrate existing SQLite data → Postgres

If you already have a local `data/app.db`:

```bash
# Postgres must be running and empty (or use --force to wipe target tables)
set DATABASE_URL=postgresql+psycopg://rcon:YOUR_PASSWORD@127.0.0.1:5432/rcon_manager
pip install -r backend/requirements.txt
python scripts/migrate_sqlite_to_postgres.py --sqlite data/app.db
```

## Server types

| Type id | Label | Notes |
|---------|--------|--------|
| `sandstorm` | Insurgency: Sandstorm | A2S + RCON listplayers, map travel, kick/ban |

Type defaults live under **Settings**. Individual servers can override preferred gamemode and quick buttons on **Servers**.

## Optional: import from an ISRT SQLite DB

If you have a local ISRT `isrt_data.db` (not shipped in this repo):

```bash
python scripts/import_isrt_db.py --isrt-db /path/to/isrt_data.db --out-db data/app.db
```

## What is not in this repository

- Local secrets (`.env`, encryption keys, live databases)
- Reverse-engineered / third-party ISRT binaries and extracted sources (`_source/`, `_isrt_src/`)
- `node_modules`, Python virtualenvs, built frontend assets
