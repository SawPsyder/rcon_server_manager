# RCON Server Manager

Docker-friendly web app for **remote game server stats & control** via **Source Query**, **Source RCON** and **HTTPS admin APIs**.

Supported server types: **Insurgency: Sandstorm** (A2S + RCON) and **Satisfactory** (HTTPS API). The architecture is pluggable — a new game is one module in `backend/app/server_types/` plus a registry entry.

**Repository:** [SawPsyder/rcon_server_manager](https://github.com/SawPsyder/rcon_server_manager)

## Stack

| Piece | Role |
|--------|------|
| **rcon-manager** | FastAPI + React SPA (one image) |
| **db** | PostgreSQL 16 (separate container, named volume) |

Local development without Docker can still use **SQLite** (default when `DATABASE_URL` / `POSTGRES_HOST` are unset).

## Features

- Single-admin authentication (session cookie)
- Multi-server CRUD with **server types** and encrypted secrets at rest
- Live server status (A2S query or HTTPS API, per type) + auto-refresh
- Continuous player-count sampling + history chart, with public share links
- Type-aware player list, kick/ban, map travel (Sandstorm)
- **Satisfactory admin panel**: server options, advanced game settings, sessions & saves, danger zone
- **Persistent connections** per transport (avoids Sandstorm per-connect thread leak; caches API bearer tokens)
- Command console with per-type command allowlist
- Hybrid config: type defaults + optional per-server preferred gamemode
- Hardcoded per-type quick command buttons

## Branches & releases

| Branch / event | Purpose |
|----------------|---------|
| `develop` | Day-to-day work |
| `master` | Stable / release line |
| Tag `vX.Y.Z` on `master` | CI builds and publishes Docker image to GHCR |

### Publish a release image

Use the helper script (recommended) — analyses commits since the last `v*` tag,
merges `develop` → `master`, creates an annotated tag, pushes, and opens a GitHub Release:

```bash
# Inspect only
python scripts/release.py analyse
python scripts/release.py analyse --bump minor

# Full release (requires clean tree, git + gh auth)
python scripts/release.py release --bump patch --yes

# Preview commands without changing remotes / GitHub
python scripts/release.py release --bump patch --dry-run
```

Manual equivalent:

```bash
git checkout master
git merge develop   # when ready
git push origin master
git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0
gh release create v0.1.0 --target master --title "RCON Server Manager v0.1.0" --notes-file notes.md
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
| `ENCRYPTION_KEY` | Fernet key for RCON passwords at rest (must be `Fernet.generate_key()` output; leave empty to auto-generate under `DATA_DIR`) |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Database credentials |
| `DATABASE_URL` | Optional full SQLAlchemy URL (Compose builds from `POSTGRES_*` when unset) |
| `DATA_DIR` | App data dir for encryption key file (default `/data`; **not** the SQL store in Compose) |
| `SESSION_HTTPS_ONLY` | Set `true` behind HTTPS reverse proxy |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | SQLAlchemy pool tuning |
| `STEAM_WEB_API_KEY` | Optional; resolve SteamID64 → persona name on ban list ([get a key](https://steamcommunity.com/dev/apikey)). Results are stored in DB `identity_cache` and reused. |
| `IDENTITY_CACHE_TTL_SECONDS` | Only used when force-refreshing stale Steam API entries (default 7 days). Cache hits with a name never require a new API call. |

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
npx tsc --noEmit    # type check only
```

### Tests

```bash
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
python -m pytest backend/tests -q
```

No game server is needed — the HTTPS API client is exercised through
`httpx.MockTransport` and the adapters through injected fakes.

## Migrate existing SQLite data → Postgres

If you already have a local `data/app.db`:

```bash
# Postgres must be running and empty (or use --force to wipe target tables)
set DATABASE_URL=postgresql+psycopg://rcon:YOUR_PASSWORD@127.0.0.1:5432/rcon_manager
pip install -r backend/requirements.txt
python scripts/migrate_sqlite_to_postgres.py --sqlite data/app.db
```

## Server types

| Type id | Label | Default port(s) | Transport | Notes |
|---------|--------|-----------------|-----------|--------|
| `sandstorm` | Insurgency: Sandstorm | 27131 query / 27015 RCON | A2S (UDP) + Source RCON (TCP) | Player list, kick/ban, map travel, ban list |
| `satisfactory` | Satisfactory | 7777 (one port) | HTTPS API (`POST /api/v1`) | Player **count** only — no roster; server options, saves & sessions, console |

Type defaults live under **Settings**. Individual servers can override preferred gamemode on **Servers**.

The UI adapts from each type's feature flags, so sections that a game cannot support are simply absent rather than broken.

### Satisfactory

Add the server under **Servers** with type *Satisfactory* and **API port** `7777` (the game port — the HTTPS API listens on the same number over TCP).

**Credentials** — the secret field accepts either, and the app detects which you gave it:

- an **API token** from the server console: `server.GenerateAPIToken` (recommended — long-lived, no password stored), or
- the **admin password**, which is exchanged for a bearer token via `PasswordLogin` and cached in memory.

Leaving it blank only works for a brand-new unclaimed server; use the admin panel's **Claim** action to claim it and store the token it returns.

**TLS** — the dedicated server generates a self-signed certificate unless you install your own under `FactoryGame/Certificates/`, so certificate verification is **off by default**. Two per-server options:

- *Verify TLS certificate* — enable only when the server presents a certificate your system trusts.
- *Pinned certificate fingerprint* — paste the SHA-256 fingerprint (any of `aa:bb:…`, `AABB…`, spaced) and the app refuses to talk to anything else. This gives real MITM protection without a CA and is the recommended setting for a self-signed server.

**Deliberate limitations** — the API exposes `numConnectedPlayers` and `playerLimit` but never individual players, so player counts and charts work while rosters, presence/playtime tracking, identity lookups and kick/ban do not exist for this type. Save upload/download are not proxied (multi-hundred-MB binary streams).

## Optional: import from an ISRT SQLite DB

If you have a local ISRT `isrt_data.db` (not shipped in this repo):

```bash
python scripts/import_isrt_db.py --isrt-db /path/to/isrt_data.db --out-db data/app.db
```

## What is not in this repository

- Local secrets (`.env`, encryption keys, live databases)
- Reverse-engineered / third-party ISRT binaries and extracted sources (`_source/`, `_isrt_src/`)
- `node_modules`, Python virtualenvs, built frontend assets
