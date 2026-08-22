"""Authorization invariants, asserted against the real route table.

Router-level guards (main.py) are cheap to add and easy to forget. These tests
turn "did we cover the new router?" from a review question into a failing test.
They only introspect the app object - no TestClient, no network - matching the
rest of the suite.
"""

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.deps import get_current_user, require_admin, require_server_scope
from app.main import app

# Everything reachable without a session, and why.
PUBLIC_PATHS = {
    "/api/health",
    # Login screen needs these before anyone is signed in.
    "/api/auth/config",
    "/api/auth/status",
    "/api/auth/bootstrap",
    "/api/auth/bootstrap-claim",
    "/api/auth/login",
    "/api/auth/login/totp",
    "/api/auth/logout",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
    "/api/auth/reset-token/check",
    # Deliberate public read-only share links.
    "/api/public/charts/{token}/meta",
    "/api/public/charts/{token}/stats",
    "/api/public/maps/{token}/meta",
    "/api/public/maps/{token}/world",
}


def _dependency_calls(dependant: Dependant) -> set:
    """Every callable resolved for a route, including nested dependencies."""
    found = set()
    stack = list(dependant.dependencies)
    while stack:
        dep = stack.pop()
        if dep.call is not None:
            found.add(dep.call)
        stack.extend(dep.dependencies)
    return found


def _api_routes() -> list[APIRoute]:
    return [
        r
        for r in app.routes
        if isinstance(r, APIRoute) and r.path.startswith("/api/")
    ]


def test_public_path_list_is_accurate():
    """Guard against a stale allow-list quietly exempting a real route."""
    known = {r.path for r in _api_routes()}
    stale = PUBLIC_PATHS - known
    assert not stale, f"PUBLIC_PATHS lists routes that no longer exist: {sorted(stale)}"


def test_every_api_route_requires_authentication():
    unguarded = []
    for route in _api_routes():
        if route.path in PUBLIC_PATHS:
            continue
        if get_current_user not in _dependency_calls(route.dependant):
            unguarded.append(f"{sorted(route.methods)} {route.path}")
    assert not unguarded, (
        "These API routes do not authenticate. Add the router to main.py with "
        f"dependencies=AUTHED or SCOPED, or list it in PUBLIC_PATHS: {unguarded}"
    )


def test_every_server_scoped_route_checks_grants():
    """A {server_id} route must verify the caller was granted that server."""
    unscoped = []
    for route in _api_routes():
        if "{server_id}" not in route.path:
            continue
        calls = _dependency_calls(route.dependant)
        if require_server_scope not in calls and require_admin not in calls:
            unscoped.append(f"{sorted(route.methods)} {route.path}")
    assert not unscoped, (
        "These per-server routes never check the caller's grant: " f"{unscoped}"
    )


def test_connection_settings_routes_are_admin_only():
    """Writing connection settings must stay out of a granted operator's reach."""
    required_admin = {
        ("POST", "/api/servers"),
        ("PUT", "/api/servers/{server_id}"),
        ("DELETE", "/api/servers/{server_id}"),
        ("PUT", "/api/settings"),
        ("GET", "/api/settings/client-ip"),
        # The panel credentials, and the inventory used to pick a link target.
        # Operators may press the power buttons; only an admin decides which
        # container they point at.
        ("GET", "/api/pterodactyl"),
        ("PUT", "/api/pterodactyl"),
        ("POST", "/api/pterodactyl/test"),
        ("GET", "/api/pterodactyl/servers"),
        # Egg startup vars change configuration that survives restarts.
        ("GET", "/api/servers/{server_id}/pterodactyl/startup"),
        ("PUT", "/api/servers/{server_id}/pterodactyl/startup/variable"),
        ("POST", "/api/servers/{server_id}/pterodactyl/default-map"),
        # These two rotate servers.rcon_password_enc from inside the game panel.
        ("POST", "/api/servers/{server_id}/satisfactory/passwords/admin"),
        ("POST", "/api/servers/{server_id}/satisfactory/claim"),
        ("GET", "/api/health/details"),
    }
    seen = set()
    for route in _api_routes():
        for method in route.methods:
            key = (method, route.path)
            if key in required_admin:
                seen.add(key)
                assert require_admin in _dependency_calls(route.dependant), (
                    f"{method} {route.path} edits connection settings but is not admin-only"
                )
    missing = required_admin - seen
    assert not missing, f"Expected these routes to exist: {sorted(missing)}"


def test_schedule_routes_are_admin_only():
    for route in _api_routes():
        if not route.path.startswith("/api/schedules"):
            continue
        assert require_admin in _dependency_calls(route.dependant), (
            f"{sorted(route.methods)} {route.path} is not admin-only"
        )


def test_user_administration_is_admin_only():

    for route in _api_routes():
        if not route.path.startswith("/api/users"):
            continue
        assert require_admin in _dependency_calls(route.dependant), (
            f"{sorted(route.methods)} {route.path} is not admin-only"
        )
