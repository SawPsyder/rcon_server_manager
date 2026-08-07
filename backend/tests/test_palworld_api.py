"""Palworld REST API client behaviour, driven through httpx.MockTransport."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.services.palworld_api import (
    ApiEndpoint,
    PalworldApiError,
    PalworldAuthError,
    PalworldClient,
    PalworldTimeoutError,
    basic_auth_header,
)

PASSWORD = "s3cret"


def make_client(
    handler,
    *,
    secret: str = PASSWORD,
    auth_cooldown_seconds: float = 30.0,
    **endpoint_kwargs,
) -> PalworldClient:
    endpoint = ApiEndpoint(host="pal.example", port=8212, secret=secret, **endpoint_kwargs)
    return PalworldClient(
        endpoint,
        transport=httpx.MockTransport(handler),
        auth_cooldown_seconds=auth_cooldown_seconds,
    )


def recorder(payload=None, *, status: int = 200, text: str | None = None):
    """Handler that records requests and answers with a fixed response."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if text is not None:
            return httpx.Response(status, text=text)
        return httpx.Response(status, json=payload)

    return handler, seen


def body_of(request: httpx.Request) -> dict:
    return json.loads(request.content.decode())


# --- auth -----------------------------------------------------------------


def test_sends_basic_auth_as_admin_and_the_admin_password():
    handler, seen = recorder({"version": "v1.0.2"})
    make_client(handler).info()

    expected = "Basic " + base64.b64encode(f"admin:{PASSWORD}".encode()).decode()
    assert seen[0].headers["Authorization"] == expected
    # The API only accepts the account name "admin"; it is not configurable
    decoded = base64.b64decode(seen[0].headers["Authorization"][6:]).decode()
    assert decoded.split(":", 1)[0] == "admin"


def test_basic_auth_header_helper_matches_the_wire_format():
    assert basic_auth_header("admin", "pw") == "Basic YWRtaW46cHc="


def test_url_carries_the_v1_api_prefix():
    # The doc pages show paths without it; generating a client from them 404s
    handler, seen = recorder({"serverfps": 60})
    make_client(handler).metrics()
    assert str(seen[0].url) == "http://pal.example:8212/v1/api/metrics"


def test_plain_http_by_default_and_https_when_asked():
    handler, seen = recorder({})
    make_client(handler).info()
    assert str(seen[0].url).startswith("http://")

    handler, seen = recorder({})
    make_client(handler, use_https=True).info()
    assert str(seen[0].url).startswith("https://")


# --- errors ---------------------------------------------------------------


def test_401_raises_auth_error_naming_the_admin_password():
    handler, _ = recorder(status=401, text="Unauthorized.")
    with pytest.raises(PalworldAuthError) as exc:
        make_client(handler).info()
    assert "AdminPassword" in str(exc.value)
    assert exc.value.status == 401


def test_401_arms_a_cooldown_so_polls_stop_hammering():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="Unauthorized.")

    client = make_client(handler)
    with pytest.raises(PalworldAuthError):
        client.info()
    with pytest.raises(PalworldAuthError) as second:
        client.metrics()
    # The second call never reaches the network
    assert calls["n"] == 1
    assert "cooldown" in str(second.value)


def test_a_successful_call_clears_the_cooldown():
    state = {"reject": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["reject"]:
            return httpx.Response(401, text="Unauthorized.")
        return httpx.Response(200, json={"version": "v1.0.2"})

    client = make_client(handler, auth_cooldown_seconds=0.0)
    with pytest.raises(PalworldAuthError):
        client.info()
    state["reject"] = False
    assert client.info()["version"] == "v1.0.2"

    state["reject"] = True
    with pytest.raises(PalworldAuthError):
        client.info()


def test_400_surfaces_the_body_snippet():
    handler, _ = recorder(status=400, text="Bad request.")
    with pytest.raises(PalworldApiError) as exc:
        make_client(handler).info()
    assert "Bad request." in str(exc.value)
    assert exc.value.status == 400
    assert not isinstance(exc.value, PalworldAuthError)


def test_timeout_is_typed():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(PalworldTimeoutError):
        make_client(handler).metrics()


def test_connect_error_names_the_url():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(PalworldApiError) as exc:
        make_client(handler).info()
    assert "pal.example:8212" in str(exc.value)


def test_tls_flavoured_connect_error_is_typed_and_actionable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("certificate verify failed", request=request)

    from app.services.palworld_api import PalworldTlsError

    with pytest.raises(PalworldTlsError) as exc:
        make_client(handler, use_https=True).info()
    # Palworld itself is plain HTTP, so name that first
    assert "plain HTTP" in str(exc.value)


# --- response tolerance ---------------------------------------------------


def test_non_json_success_body_is_returned_as_text():
    # POST success bodies are undocumented and not always JSON
    handler, _ = recorder(status=200, text="The message was announced.")
    assert make_client(handler).announce("hi") == "The message was announced."


def test_empty_success_body_reads_as_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    assert make_client(handler).save() == "ok"


def test_json_object_body_prefers_a_message_field():
    handler, _ = recorder({"message": "Successfully saved the world."})
    assert make_client(handler).save() == "Successfully saved the world."


def test_players_tolerates_a_missing_or_malformed_list():
    for payload in ({}, {"players": None}, {"players": "nope"}):
        handler, _ = recorder(payload)
        assert make_client(handler).players() == []


def test_players_drops_non_object_entries():
    handler, _ = recorder({"players": [{"name": "Lyra"}, "junk", None]})
    assert make_client(handler).players() == [{"name": "Lyra"}]


# --- request shapes -------------------------------------------------------


def test_bodyless_posts_send_content_length_zero():
    # Some HTTP stacks omit the header otherwise, and the server is picky
    handler, seen = recorder(status=200, text="Successfully saved the world.")
    make_client(handler).save()
    assert seen[0].method == "POST"
    assert seen[0].headers["Content-Length"] == "0"
    assert str(seen[0].url).endswith("/v1/api/save")


def test_kick_and_ban_send_lowercase_userid():
    # The community OpenAPI spec says "userId" - it is wrong
    handler, seen = recorder(status=200, text="ok")
    client = make_client(handler)
    client.kick("steam_76561198084350159", "bye")
    client.ban("steam_76561198084350159")

    assert body_of(seen[0]) == {"userid": "steam_76561198084350159", "message": "bye"}
    # message is optional and omitted rather than sent empty
    assert body_of(seen[1]) == {"userid": "steam_76561198084350159"}


def test_unban_sends_only_the_userid():
    handler, seen = recorder(status=200, text="ok")
    make_client(handler).unban("steam_76561198084350159")
    assert body_of(seen[0]) == {"userid": "steam_76561198084350159"}


def test_shutdown_sends_waittime_and_optional_message():
    handler, seen = recorder(status=200, text="ok")
    client = make_client(handler)
    client.shutdown(30, "Restarting")
    client.shutdown(5)
    assert body_of(seen[0]) == {"waittime": 30, "message": "Restarting"}
    assert body_of(seen[1]) == {"waittime": 5}


def test_announce_sends_the_message():
    handler, seen = recorder(status=200, text="ok")
    make_client(handler).announce("Server restarting")
    assert body_of(seen[0]) == {"message": "Server restarting"}


# --- game-data ------------------------------------------------------------


def test_game_data_disabled_is_recognised_from_a_200_text_body():
    # Without -enable-gamedata-api the server refuses in the body, not the status
    handler, _ = recorder(status=200, text="GameData API is not enabled")
    with pytest.raises(PalworldApiError) as exc:
        make_client(handler).game_data()
    assert exc.value.code == "gamedata_disabled"
    assert "-enable-gamedata-api" in str(exc.value)


def test_game_data_disabled_is_recognised_from_an_error_status_too():
    handler, _ = recorder(status=400, text="GameData API is not enabled")
    with pytest.raises(PalworldApiError) as exc:
        make_client(handler).game_data()
    assert exc.value.code == "gamedata_disabled"


def test_game_data_auth_failure_stays_an_auth_error():
    handler, _ = recorder(status=401, text="Unauthorized.")
    with pytest.raises(PalworldAuthError):
        make_client(handler).game_data()


def test_game_data_returns_the_payload_when_enabled():
    handler, _ = recorder({"Time": "2026-08-07 12:00:00", "ActorData": []})
    assert make_client(handler).game_data()["ActorData"] == []


# --- endpoint identity ----------------------------------------------------


def test_endpoint_key_separates_every_credential_and_scheme_change():
    base = ApiEndpoint(host="pal.example", port=8212, secret="a")
    variants = [
        base,
        ApiEndpoint(host="pal.example", port=8212, secret="b"),
        ApiEndpoint(host="pal.example", port=8213, secret="a"),
        ApiEndpoint(host="pal.example", port=8212, secret="a", use_https=True),
        ApiEndpoint(host="pal.example", port=8212, secret="a", verify_tls=True),
        ApiEndpoint(host="pal.example", port=8212, secret="a", cert_fingerprint="aa"),
        ApiEndpoint(host="pal.example", port=8212, secret="a", username="root"),
    ]
    assert len({v.key for v in variants}) == len(variants)


def test_fingerprint_formatting_does_not_split_pool_entries():
    a = ApiEndpoint(host="h", secret="s", cert_fingerprint="AA:BB:CC")
    b = ApiEndpoint(host="h", secret="s", cert_fingerprint="aabbcc")
    assert a.key == b.key
