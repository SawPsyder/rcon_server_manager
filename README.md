# RCON Server Manager

A self-hosted **web admin dashboard** for managing game servers from one place.

> **Note:** This project is mostly vibe-coded with [Grok](https://x.ai) and [Claude](https://claude.ai).

**Image:** [`ghcr.io/sawpsyder/rcon_server_manager`](https://github.com/SawPsyder/rcon_server_manager/pkgs/container/rcon_server_manager)  
**Source:** [SawPsyder/rcon_server_manager](https://github.com/SawPsyder/rcon_server_manager)

![RCON Server Manager UI snapshot](sample.png)

---

## Features

<!-- FEATURES:BEGIN -->
- Password-protected admin dashboard
- Manage multiple game servers from one UI
- Live status and automatic refresh
- Player-count history with public chart share links
- Multi-server overview
- Encrypted server credentials at rest
- Optional Steam persona name lookup
<!-- FEATURES:END -->

Game-specific admin tools (player control, map travel, saves, etc.) depend on the server type - see below.

---

## Supported games

### Insurgency: Sandstorm

Connects via **Source Query + RCON** (defaults: query `27131`, RCON `27015`).

- Live status and player list
- Kick, ban, and ban list management
- Admin broadcast messages
- Map / gamemode travel and quick commands
- RCON console
- Player history and playtime tracking

### Satisfactory

Connects via the dedicated server **HTTPS API** on the game port (default `7777`). Use an admin password or API token.

- Live status and player counts (no per-player roster - the game API does not expose one)
- Player-count and tick-rate history
- Admin panel: options, advanced settings, sessions & saves, claim / rename / shutdown
- Optional certificate fingerprint pin for self-signed TLS

### Palworld

Connects via the dedicated server **REST API** (default port `8212`) - not RCON, which Pocketpair has deprecated in favour of the API. Use the server's `AdminPassword`.

- Live status, player counts, and a full player roster
- Player-count and **server FPS** history
- Kick, ban, and unban (bans are permanent - the API takes no duration)
- Admin broadcast messages
- Admin panel: per-player detail, read-only settings, world snapshot, save, graceful shutdown, force stop
- Player history and playtime tracking, including crossplay (Steam, Xbox / Game Pass, PlayStation)

Enable the API in `PalWorldSettings.ini` (there is no launch argument for it) and restart the server:

```ini
RESTAPIEnabled=True,RESTAPIPort=8212,AdminPassword="your-password"
```

Two optional extras:

- The **World** tab needs the server launched with `-enable-gamedata-api`; without it the tab explains how to turn it on.
- Palworld serves **plain HTTP** and has no TLS of its own. Pocketpair warns these endpoints "are not designed to be exposed directly to the Internet" - keep port `8212` firewalled to trusted hosts. If you front it with a TLS-terminating reverse proxy, tick **Use HTTPS** on the server and optionally pin its certificate fingerprint.

No ban list is shown for Palworld: the REST API has no endpoint for it, and bans live in `banlist.txt` on the server's disk.

---

## Quick start

Use this Docker Compose stack with **Portainer**, plain `docker compose`, or any other Compose-compatible setup. Set the variables listed under [Environment variables](#environment-variables), and change the `/change/me/...` volume paths to storage on your host. After start, open port **8080** and sign in with `ADMIN_PASSWORD`.

```yaml
services:
  db:
    image: postgres:16-alpine
    container_name: rcon-manager-db
    restart: unless-stopped
    networks:
      - rcon_manager
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - /change/me/sql:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
      interval: 5s
      timeout: 5s
      retries: 12
      start_period: 10s

  rcon-manager:
    image: ghcr.io/sawpsyder/rcon_server_manager:latest
    container_name: rcon-manager
    restart: unless-stopped
    networks:
      - rcon_manager
    ports:
      - "8080:8080"
    depends_on:
      db:
        condition: service_healthy
    environment:
      ADMIN_PASSWORD: ${ADMIN_PASSWORD}
      SECRET_KEY: ${SECRET_KEY}
      DATA_DIR: /data
      SESSION_HTTPS_ONLY: ${SESSION_HTTPS_ONLY:-false}
      POSTGRES_HOST: db
      POSTGRES_PORT: "5432"
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
      DB_POOL_SIZE: ${DB_POOL_SIZE:-5}
      DB_MAX_OVERFLOW: ${DB_MAX_OVERFLOW:-10}
      STEAM_WEB_API_KEY: ${STEAM_WEB_API_KEY}
    volumes:
      - /change/me/rcon_manager:/data

networks:
  rcon_manager:
```

---

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ADMIN_PASSWORD` | Yes (first boot) | Initial dashboard password (hashed and stored; changing the env later does not reset it by itself) |
| `SECRET_KEY` | Yes | Secret used to sign session cookies |
| `POSTGRES_HOST` | Yes (Compose) | Database hostname (`db` in the sample) |
| `POSTGRES_PORT` | No | Default `5432` |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Yes | Postgres credentials (must match the `db` service) |
| `DATA_DIR` | Recommended | App data directory inside the container (sample uses `/data`) |
| `ENCRYPTION_KEY` | No | Fernet key for encrypting stored server secrets; auto-created under `DATA_DIR` if empty |
| `SESSION_HTTPS_ONLY` | No | Set `true` when the app is only reached over HTTPS (reverse proxy) |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | No | SQLAlchemy pool size (defaults `5` / `10`) |
| `STEAM_WEB_API_KEY` | No | Steam Web API key for persona name lookup |
| `DATABASE_URL` | No | Full SQLAlchemy URL; if unset, built from `POSTGRES_*` when `POSTGRES_HOST` is set |
