"""Dune admin-HTTP client: login cache, Bearer, player-table parse."""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.dune_api import (
    ApiEndpoint,
    DuneApiError,
    DuneAuthError,
    DuneClient,
    dedupe_player_rows,
    parse_player_table,
    row_is_online,
    settings_server_info,
)

PASSWORD = "ui-password"
TOKEN = "hmac.token.value"


def make_client(handler, *, secret: str = PASSWORD, **kwargs) -> DuneClient:
    endpoint = ApiEndpoint(host="dune.example", port=8090, secret=secret)
    return DuneClient(
        endpoint,
        transport=httpx.MockTransport(handler),
        auth_cooldown_seconds=30.0,
        **kwargs,
    )


def body_of(request: httpx.Request) -> dict:
    return json.loads(request.content.decode()) if request.content else {}


PLAYERS_STDOUT = (
    " fls_id | character | steam_id | platform_name | life | online | last_avatar_activity \n"
    "--------+-----------+----------+---------------+------+--------+----------------------\n"
    " DE0BCCAA2501BF22 | Sergentval | 76561198041278656 | Steam | Alive | Online | 2026-05-28 07:22:05.861+00\n"
    "(1 row)\n"
)


def test_parse_player_table_reads_fls_and_steam():
    rows = parse_player_table(PLAYERS_STDOUT)
    assert len(rows) == 1
    assert rows[0]["fls_id"] == "DE0BCCAA2501BF22"
    assert rows[0]["character"] == "Sergentval"
    assert rows[0]["steam_id"] == "76561198041278656"
    assert rows[0]["online"] == "Online"


LIVE_DUP_PLAYERS_STDOUT = (
    "      fls_id      | character |     steam_id      | platform_name | life  | online | last_avatar_activity \n"
    "------------------+-----------+-------------------+---------------+-------+--------+----------------------\n"
    " 3A9443BD8F1E46CD |           | 76561198067446355 | Steam         | Alive | Online | \n"
    " 3A9443BD8F1E46CD | Jay       | 76561198067446355 | Steam         | Alive | Online | \n"
    "(2 rows)\n"
)

# Same account, now logged off. The named row tracks the real session; the
# nameless leftover's online_status is stuck at 'Online' forever. Captured
# live from the egg (2026-08-19) - /api/map/markers, which reads the plaintext
# dune.player_state instead, agreed the player was offline.
LIVE_OFFLINE_PLAYERS_STDOUT = (
    "      fls_id      | character |     steam_id      | platform_name | life  | online  |    last_avatar_activity    \n"
    "------------------+-----------+-------------------+---------------+-------+---------+----------------------------\n"
    " 3A9443BD8F1E46CD | Jay       | 76561198067446355 | Steam         | Alive | Offline | 2026-08-18 22:53:16.785+00\n"
    " 3A9443BD8F1E46CD |           | 76561198067446355 | Steam         | Alive | Online  | \n"
    "(2 rows)\n"
)

# What `players online` returns for that same account: the leftover only.
LIVE_STUCK_ONLINE_STDOUT = (
    "      fls_id      | character |     steam_id      | platform_name | life  | online | last_avatar_activity \n"
    "------------------+-----------+-------------------+---------------+-------+--------+----------------------\n"
    " 3A9443BD8F1E46CD |           | 76561198067446355 | Steam         | Alive | Online | \n"
    "(1 row)\n"
)


def test_resolved_row_online_ignores_the_nameless_leftover():
    rows = parse_player_table(LIVE_OFFLINE_PLAYERS_STDOUT)
    assert len(rows) == 1
    assert rows[0]["character"] == "Jay"
    assert row_is_online(rows[0]) is False


def test_resolved_row_online_when_the_character_is_connected():
    rows = parse_player_table(LIVE_DUP_PLAYERS_STDOUT)
    assert row_is_online(rows[0]) is True


def test_parse_player_table_collapses_duplicate_fls_and_keeps_character():
    rows = parse_player_table(LIVE_DUP_PLAYERS_STDOUT)
    assert len(rows) == 1
    assert rows[0]["fls_id"] == "3A9443BD8F1E46CD"
    assert rows[0]["character"] == "Jay"
    assert rows[0]["steam_id"] == "76561198067446355"


def test_dedupe_merges_name_onto_nameless_row():
    rows = dedupe_player_rows(
        [
            {
                "fls_id": "AABBCCDDEEFF0011",
                "character": "",
                "steam_id": "76561198000000000",
                "platform_name": "Steam",
                "life": "Alive",
                "online": "Online",
                "last_avatar_activity": "",
            },
            {
                "fls_id": "AABBCCDDEEFF0011",
                "character": "Stilgar",
                "steam_id": "76561198000000000",
                "platform_name": "Steam",
                "life": "Alive",
                "online": "Online",
                "last_avatar_activity": "2026-08-19 10:00:00+00",
            },
        ]
    )
    assert len(rows) == 1
    assert rows[0]["character"] == "Stilgar"


def test_dedupe_keeps_distinct_accounts():
    rows = dedupe_player_rows(
        [
            {
                "fls_id": "AAAAAAAAAAAAAAA1",
                "character": "One",
                "steam_id": "76561198000000001",
                "platform_name": "Steam",
                "life": "Alive",
                "online": "Online",
                "last_avatar_activity": "",
            },
            {
                "fls_id": "AAAAAAAAAAAAAAA2",
                "character": "Two",
                "steam_id": "76561198000000002",
                "platform_name": "Steam",
                "life": "Alive",
                "online": "Online",
                "last_avatar_activity": "",
            },
        ]
    )
    assert [r["character"] for r in rows] == ["One", "Two"]


def test_parse_player_table_empty():
    empty = (
        " fls_id | character | steam_id | platform_name | life | online | last_avatar_activity \n"
        "--------+-----------+----------+---------------+------+--------+----------------------\n"
        "(0 rows)\n"
    )
    assert parse_player_table(empty) == []


def test_login_then_bearer_on_status():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/login":
            return httpx.Response(
                200, json={"token": TOKEN, "csrf": "x", "expires_in": 604800, "type": "Bearer"}
            )
        if request.url.path == "/api/status":
            return httpx.Response(200, json={"ok": True, "totalPlayers": 0, "maps": []})
        return httpx.Response(404, json={"error": "missing"})

    grid = make_client(handler).status()
    assert grid["ok"] is True
    assert seen[0].url.path == "/api/login"
    assert body_of(seen[0]) == {"password": PASSWORD}
    assert seen[1].url.path == "/api/status"
    assert seen[1].headers["Authorization"] == f"Bearer {TOKEN}"


def test_token_is_reused_across_calls():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": TOKEN, "expires_in": 604800})
        return httpx.Response(200, json={"ok": True, "maps": []})

    client = make_client(handler)
    client.status()
    client.status()
    logins = [r for r in seen if r.url.path == "/api/login"]
    assert len(logins) == 1
    assert len(seen) == 3


def test_401_on_status_relogs_once():
    seen: list[httpx.Request] = []
    status_hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": TOKEN, "expires_in": 604800})
        status_hits["n"] += 1
        if status_hits["n"] == 1:
            return httpx.Response(401, json={"error": "auth required"})
        return httpx.Response(200, json={"ok": True, "maps": []})

    assert make_client(handler).status()["ok"] is True
    assert [r.url.path for r in seen] == [
        "/api/login",
        "/api/status",
        "/api/login",
        "/api/status",
    ]


def test_bad_password_raises_auth_error_and_cools_down():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad password"})

    client = make_client(handler)
    with pytest.raises(DuneAuthError) as exc:
        client.status()
    assert "admin UI password" in str(exc.value)
    with pytest.raises(DuneAuthError) as cooled:
        client.status()
    assert "cooldown" in str(cooled.value)


def test_rate_limited_login_cools_down():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "too many"}, headers={"Retry-After": "90"})

    with pytest.raises(DuneAuthError) as exc:
        make_client(handler).status()
    assert exc.value.status == 429
    assert "rate-limited" in str(exc.value)


def test_players_parses_publish_stdout():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": TOKEN, "expires_in": 604800})
        return httpx.Response(
            200,
            json={
                "ok": True,
                "argv": ["players", "online"],
                "stdout": PLAYERS_STDOUT,
                "stderr": "",
            },
        )

    rows = make_client(handler).players("online")
    assert rows[0]["fls_id"] == "DE0BCCAA2501BF22"


def test_players_online_drops_the_stuck_leftover_row():
    """The SQL filter alone reports a logged-off player as online."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": TOKEN, "expires_in": 604800})
        kind = request.url.params.get("filter", "")
        seen.append(kind)
        stdout = (
            LIVE_OFFLINE_PLAYERS_STDOUT if kind == "all" else LIVE_STUCK_ONLINE_STDOUT
        )
        return httpx.Response(200, json={"ok": True, "stdout": stdout, "stderr": ""})

    client = make_client(handler)
    assert client.players("online") == []
    assert "all" in seen
    # `players all` still lists the account, resolved to its real status.
    everyone = client.players("all")
    assert [r["character"] for r in everyone] == ["Jay"]
    assert everyone[0]["online"] == "Offline"


def test_players_online_keeps_accounts_the_all_window_missed():
    """The `all` read is LIMIT 100 too, so never lose a player to it."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": TOKEN, "expires_in": 604800})
        kind = request.url.params.get("filter", "")
        stdout = LIVE_OFFLINE_PLAYERS_STDOUT if kind == "all" else PLAYERS_STDOUT
        return httpx.Response(200, json={"ok": True, "stdout": stdout, "stderr": ""})

    rows = make_client(handler).players("online")
    assert [r["fls_id"] for r in rows] == ["DE0BCCAA2501BF22"]


def test_settings_server_info_reads_name_and_cap():
    info = settings_server_info(
        {
            "categories": {
                "World Identity": [
                    {"key": "Bgd.ServerDisplayName", "value": "Arrakis", "default": ""},
                ],
                "Server": [
                    {"key": "Bgd.ServerPlayerHardCap", "value": None, "default": "40"},
                ],
            }
        }
    )
    assert info == {"display_name": "Arrakis", "player_hard_cap": 40}


def test_settings_server_info_leaves_unset_cap_none():
    info = settings_server_info(
        {
            "categories": {
                "Server": [
                    {"key": "Bgd.ServerPlayerHardCap", "value": None, "default": ""}
                ]
            }
        }
    )
    assert info["player_hard_cap"] is None
    assert settings_server_info({}) == {"display_name": "", "player_hard_cap": None}


def test_server_info_is_cached_and_survives_a_failed_read():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": TOKEN, "expires_in": 604800})
        calls.append(request.url.path)
        if len(calls) > 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(
            200,
            json={
                "categories": {
                    "Server": [
                        {"key": "Bgd.ServerPlayerHardCap", "value": "60", "default": ""}
                    ]
                }
            },
        )

    client = make_client(handler)
    assert client.server_info()["player_hard_cap"] == 60
    # A second call inside the TTL never touches the network.
    assert client.server_info()["player_hard_cap"] == 60
    assert len(calls) == 1
    # A stale-but-good answer beats blanking the card.
    assert client.server_info(max_age=0)["player_hard_cap"] == 60
    assert len(calls) == 2


def test_login_cookies_never_ride_along_on_later_calls():
    """The egg reads the session cookie BEFORE the Bearer header.

    /api/login answers with Set-Cookie, and a cookie-borne session must also
    present X-CSRF-Token. If httpx's jar replays that cookie we get silently
    downgraded to cookie auth and every mutation 403s with "csrf token missing
    or invalid" — while reads, which skip the CSRF gate, keep working.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/login":
            return httpx.Response(
                200,
                json={"token": TOKEN, "expires_in": 604800},
                headers=[
                    ("Set-Cookie", "dune_session=sess-value; Path=/; HttpOnly"),
                    ("Set-Cookie", "dune_csrf=csrf-value; Path=/"),
                ],
            )
        return httpx.Response(200, json={"ok": True, "stdout": "publish=ok", "stderr": ""})

    client = make_client(handler)
    client.broadcast("Maint", "Restart in 5")

    post = seen[-1]
    assert post.url.path == "/admin/broadcast"
    assert "cookie" not in {k.lower() for k in post.headers}
    assert post.headers["Authorization"] == f"Bearer {TOKEN}"
    # Belt and braces: if a reverse proxy ever re-attaches the cookie, the
    # header the egg wants is already on the request.
    assert post.headers["X-CSRF-Token"] == "csrf-value"


def test_csrf_header_is_not_sent_on_reads():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(
                200,
                json={"token": TOKEN, "expires_in": 604800},
                headers=[("Set-Cookie", "dune_csrf=csrf-value; Path=/")],
            )
        assert "x-csrf-token" not in {k.lower() for k in request.headers}
        return httpx.Response(200, json={"ok": True})

    make_client(handler).status()


def test_broadcast_posts_admin_path():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": TOKEN, "expires_in": 604800})
        return httpx.Response(200, json={"ok": True, "stdout": "publish=ok", "stderr": ""})

    make_client(handler).broadcast("Maint", "Restart in 5", 20)
    post = seen[-1]
    assert post.url.path == "/admin/broadcast"
    assert body_of(post) == {"title": "Maint", "body": "Restart in 5", "duration": 20}


def test_failed_publish_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": TOKEN, "expires_in": 604800})
        return httpx.Response(
            200, json={"ok": False, "stdout": "", "stderr": "player not found"}
        )

    with pytest.raises(DuneApiError) as exc:
        make_client(handler).kick("DEADBEEFDEADBEEF")
    assert "player not found" in str(exc.value)


def test_https_when_asked():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"token": TOKEN, "expires_in": 604800})
        return httpx.Response(200, json={"ok": True})

    endpoint = ApiEndpoint(host="dune.example", port=8090, secret=PASSWORD, use_https=True)
    DuneClient(endpoint, transport=httpx.MockTransport(handler)).status()
    assert str(seen[0].url).startswith("https://")
