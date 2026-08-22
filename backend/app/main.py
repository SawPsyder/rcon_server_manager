from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api import (
    auth,
    chart_share,
    dune,
    identities,
    mail,
    map_share,
    maps,
    palworld,
    player_leaderboard,
    pterodactyl,
    rcon,
    satisfactory,
    server_pterodactyl,
    servers,
    schedules,
    settings,
    stats,
    status,
    users,
)
from app.bootstrap import ensure_admin, seed_if_empty
from app.database import Base, SessionLocal, engine, wait_for_database
from app.deps import AdminUser, get_current_user, require_server_scope
from app.migrate import run_migrations
from app.services.pterodactyl_poller import poller as pterodactyl_poller
from app.services.schedule_runner import runner as schedule_runner
from app.services.stats_collector import collector

app = FastAPI(title="RCON Server Manager", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8080", "http://127.0.0.1:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Authorization policy, in one place.
#
# SCOPED authenticates the caller and, on any route that declares a {server_id}
# path parameter, requires a grant for that server. Routes in the same router
# without one are authenticated only.
# AUTHED authenticates and nothing more.
#
# Routers are guarded here rather than route-by-route so the policy is readable
# at a glance and new routes are covered by default. Individual admin-only
# routes add Depends(require_admin) on top. tests/test_authz_coverage.py
# asserts that nothing slips through.
SCOPED = [Depends(require_server_scope)]
AUTHED = [Depends(get_current_user)]

app.include_router(auth.router)  # public and self-service; guarded per route
app.include_router(users.router)  # admin-only; guarded per route
app.include_router(mail.router)  # admin-only; guarded per route
app.include_router(pterodactyl.router)  # admin-only; guarded per route
app.include_router(schedules.router)  # admin-only; guarded per route
app.include_router(servers.router, dependencies=SCOPED)
app.include_router(status.router, dependencies=SCOPED)
app.include_router(stats.router, dependencies=SCOPED)
app.include_router(chart_share.admin_router, dependencies=SCOPED)
app.include_router(chart_share.public_router)  # public share links
app.include_router(map_share.admin_router, dependencies=SCOPED)
app.include_router(map_share.public_router)  # public share links
app.include_router(rcon.router, dependencies=SCOPED)
app.include_router(maps.router, dependencies=AUTHED)
app.include_router(settings.router, dependencies=AUTHED)
app.include_router(identities.router, dependencies=AUTHED)
app.include_router(player_leaderboard.router, dependencies=AUTHED)
app.include_router(satisfactory.router, dependencies=SCOPED)
app.include_router(palworld.router, dependencies=SCOPED)
app.include_router(dune.router, dependencies=SCOPED)
app.include_router(server_pterodactyl.router, dependencies=SCOPED)


@app.on_event("startup")
def on_startup() -> None:
    from app.config import get_settings

    settings = get_settings()
    if settings.secret_key in {"", "dev-secret-change-me"}:
        # Forgeable sessions if this ships to a real host with the baked default.
        import logging

        logging.getLogger(__name__).warning(
            "SECRET_KEY is the insecure default. Set SECRET_KEY to a long random "
            "value before exposing this process to a network."
        )
    wait_for_database()
    Base.metadata.create_all(bind=engine)
    run_migrations(engine)
    db = SessionLocal()
    try:
        ensure_admin(db)
        seed_if_empty(db)
    finally:
        db.close()
    collector.start()
    pterodactyl_poller.start()
    schedule_runner.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    collector.stop()
    pterodactyl_poller.stop()
    schedule_runner.stop()
    from app.services.dune_api import dune_pool
    from app.services.palworld_api import palworld_pool
    from app.services.pterodactyl_api import panel_registry
    from app.services.rcon_pool import rcon_pool
    from app.services.satisfactory_api import satisfactory_pool

    rcon_pool.invalidate_all()
    satisfactory_pool.invalidate_all()
    palworld_pool.invalidate_all()
    dune_pool.invalidate_all()
    panel_registry.invalidate_all()


def _pool_stats() -> dict:
    from app.config import get_settings
    from app.services.dune_api import dune_pool
    from app.services.palworld_api import palworld_pool
    from app.services.pterodactyl_api import panel_registry
    from app.services.rcon_pool import rcon_pool
    from app.services.satisfactory_api import satisfactory_pool

    settings = get_settings()
    return {
        "status": "ok",
        "database": "sqlite" if settings.is_sqlite else "postgresql" if settings.is_postgres else "other",
        "rcon_sessions": rcon_pool.stats(),
        "api_sessions": satisfactory_pool.stats(),
        "palworld_sessions": palworld_pool.stats(),
        "dune_sessions": dune_pool.stats(),
        "pterodactyl_sessions": panel_registry.stats(),
    }


@app.get("/api/health")
def health() -> dict:
    """Liveness only. Pool hosts, ports, and last_error stay off this payload."""
    return {"status": "ok"}


@app.get("/api/health/details")
def health_details(_admin: AdminUser) -> dict:
    """Live connection-pool stats. Admin-only: hosts and last_error are recon."""
    return _pool_stats()


def _resolve_static_dir() -> Path | None:
    """Locate built SPA assets (Docker image path first, then local dev copies)."""
    here = Path(__file__).resolve().parent
    candidates = [
        here / "static",  # Docker / packaged: backend/app/static
        here.parent / "static",  # backend/static
        here.parent.parent / "frontend" / "dist",  # repo frontend/dist
    ]
    for path in candidates:
        if (path / "index.html").is_file():
            return path
    return None


STATIC_DIR = _resolve_static_dir()


def _index_html() -> FileResponse:
    assert STATIC_DIR is not None
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/", include_in_schema=False, response_model=None)
def root_entry():
    if STATIC_DIR is None:
        return HTMLResponse(
            content=(
                "<!doctype html><html><body style='font-family:sans-serif;background:#0d1117;color:#e7eef8;"
                "display:grid;place-items:center;min-height:100vh;margin:0'>"
                "<div style='max-width:32rem;padding:2rem'>"
                "<h1>RCON Server Manager API</h1>"
                "<p>Frontend assets were not found. Build the UI and restart:</p>"
                "<pre style='background:#151b24;padding:1rem;border-radius:8px'>"
                "cd frontend\nnpm install\nnpm run build\n"
                "Copy-Item -Recurse frontend/dist/* backend/app/static/\n"
                "# or: docker compose up --build"
                "</pre>"
                "<p>API health: <a href='/api/health' style='color:#e8a23a'>/api/health</a></p>"
                "</div></body></html>"
            ),
            status_code=200,
        )
    return _index_html()


if STATIC_DIR is not None:
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/favicon.ico", include_in_schema=False, response_model=None)
    def favicon():
        fav = STATIC_DIR / "favicon.ico"
        if fav.is_file():
            return FileResponse(fav)
        return Response(status_code=204)

    @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
    def spa_fallback(full_path: str):
        # Never hijack API routes (safety if registration order changes)
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")

        # Serve real static files (index.html siblings, robots.txt, etc.)
        candidate = (STATIC_DIR / full_path).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Not Found") from exc

        if candidate.is_file():
            return FileResponse(candidate)

        # SPA client-side routes (e.g. /servers, /settings)
        return _index_html()
else:
    @app.get("/index.html", include_in_schema=False, response_model=None)
    def missing_index():
        return root_entry()
