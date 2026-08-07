"""Satisfactory HTTPS API client behaviour, driven through httpx.MockTransport."""

from __future__ import annotations

import httpx
import pytest

from app.services.satisfactory_api import (
    ApiEndpoint,
    SatisfactoryApiError,
    SatisfactoryAuthError,
    SatisfactoryClient,
    SatisfactoryTimeoutError,
    format_fingerprint,
    looks_like_api_token,
    normalize_fingerprint,
    pick,
)

TOKEN = "eyJwbCI6IkFkbWluaXN0cmF0b3IifQ.a1b2c3d4e5f6"


def make_client(handler, secret: str = TOKEN, **kwargs) -> SatisfactoryClient:
    endpoint = ApiEndpoint(host="game.example", port=7777, secret=secret)
    return SatisfactoryClient(
        endpoint, transport=httpx.MockTransport(handler), **kwargs
    )


def json_response(payload, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def bodies(requests: list[httpx.Request]) -> list[dict]:
    import json

    return [json.loads(r.content.decode()) for r in requests]


# --- envelope handling ----------------------------------------------------


def test_returns_data_object_and_sends_bearer_token():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return json_response({"data": {"serverGameState": {"numConnectedPlayers": 3}}})

    client = make_client(handler)
    state = client.query_server_state()

    assert state == {"numConnectedPlayers": 3}
    assert str(seen[0].url) == "https://game.example:7777/api/v1"
    assert seen[0].method == "POST"
    assert seen[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert bodies(seen)[0] == {"function": "QueryServerState"}


def test_204_is_not_an_error_and_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    client = make_client(handler)
    assert client.save_game("world") is None


def test_response_keys_are_read_case_insensitively():
    def handler(request: httpx.Request) -> httpx.Response:
        # The shipped docs use PascalCase; live servers answer camelCase
        return json_response(
            {"Data": {"ServerGameState": {"NumConnectedPlayers": 2, "PlayerLimit": 4}}}
        )

    client = make_client(handler)
    state = client.query_server_state()
    assert pick(state, "numConnectedPlayers") == 2
    assert pick(state, "playerLimit") == 4


def test_unwrapped_payload_is_tolerated():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response({"health": "healthy", "serverCustomData": ""})

    client = make_client(handler)
    assert client.health_check()["health"] == "healthy"


def test_enumerate_sessions_keeps_a_zero_current_index():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {"data": {"sessions": [{"sessionName": "A"}], "currentSessionIndex": 0}}
        )

    client = make_client(handler)
    result = client.enumerate_sessions()
    # 0 is the first session, not "no session" — a falsy-default bug once made it -1
    assert result["current_session_index"] == 0
    assert result["sessions"] == [{"sessionName": "A"}]


def test_enumerate_sessions_defaults_when_index_is_missing_or_junk():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response({"data": {"sessions": [], "currentSessionIndex": "n/a"}})

    client = make_client(handler)
    assert client.enumerate_sessions()["current_session_index"] == -1


def test_health_check_is_sent_without_credentials():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return json_response({"data": {"health": "slow"}})

    client = make_client(handler, secret="")
    assert client.health_check()["health"] == "slow"
    assert "Authorization" not in seen[0].headers


# --- errors --------------------------------------------------------------


def test_error_body_becomes_api_error_with_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {"errorCode": "save_game_failed", "errorMessage": "disk full"}, status=500
        )

    client = make_client(handler)
    with pytest.raises(SatisfactoryApiError) as exc:
        client.save_game("world")
    assert exc.value.code == "save_game_failed"
    assert "disk full" in str(exc.value)
    assert not isinstance(exc.value, SatisfactoryAuthError)


def test_http_error_without_body_still_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal boom")

    client = make_client(handler)
    with pytest.raises(SatisfactoryApiError) as exc:
        client.query_server_state()
    assert "internal boom" in str(exc.value)


def test_timeout_is_mapped():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("too slow", request=request)

    client = make_client(handler)
    with pytest.raises(SatisfactoryTimeoutError):
        client.query_server_state()


def test_forbidden_is_an_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response({"errorCode": "insufficient_privilege"}, status=403)

    client = make_client(handler)
    with pytest.raises(SatisfactoryAuthError):
        client.shutdown()


# --- authentication ------------------------------------------------------


def test_password_secret_logs_in_then_reuses_the_token():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        import json

        function = json.loads(request.content.decode())["function"]
        if function == "PasswordLogin":
            return json_response({"data": {"authenticationToken": "issued.aa11"}})
        return json_response({"data": {"serverGameState": {}}})

    client = make_client(handler, secret="hunter2")
    client.query_server_state()
    client.query_server_state()

    sent = bodies(seen)
    assert [b["function"] for b in sent] == [
        "PasswordLogin",
        "QueryServerState",
        "QueryServerState",
    ], "the token must be cached, not re-fetched per call"
    assert sent[0]["data"] == {
        "minimumPrivilegeLevel": "Administrator",
        "password": "hunter2",
    }
    assert seen[1].headers["Authorization"] == "Bearer issued.aa11"
    assert client.token_kind == "login"


def test_expired_login_token_triggers_one_reauth_and_retry():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        function = json.loads(request.content.decode())["function"]
        calls.append(function)
        if function == "PasswordLogin":
            return json_response({"data": {"authenticationToken": f"t{len(calls)}.aa11"}})
        # First state call rejects the token, the retry succeeds
        if calls.count("QueryServerState") == 1:
            return json_response({"errorCode": "expired_token"}, status=401)
        return json_response({"data": {"serverGameState": {"playerLimit": 4}}})

    client = make_client(handler, secret="hunter2")
    assert client.query_server_state() == {"playerLimit": 4}
    assert calls == [
        "PasswordLogin",
        "QueryServerState",
        "PasswordLogin",
        "QueryServerState",
    ]


def test_static_api_token_is_not_retried_on_rejection():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return json_response({"errorCode": "invalid_token"}, status=401)

    client = make_client(handler, secret=TOKEN)
    with pytest.raises(SatisfactoryAuthError):
        client.query_server_state()
    assert len(calls) == 1, "a static token cannot be refreshed, so do not retry"
    assert client.token_kind == "api"


def test_empty_secret_uses_passwordless_login():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return json_response({"data": {"authenticationToken": "pl.aa11"}})

    client = make_client(handler, secret="")
    client.verify_token()
    sent = bodies(seen)
    assert sent[0]["function"] == "PasswordlessLogin"
    assert sent[0]["data"] == {"minimumPrivilegeLevel": "Administrator"}


def test_claimed_server_without_secret_gives_actionable_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {"errorCode": "passwordless_login_not_possible"}, status=400
        )

    client = make_client(handler, secret="")
    with pytest.raises(SatisfactoryAuthError) as exc:
        client.query_server_state()
    assert "admin password or an API token" in str(exc.value)


def test_rejected_login_enters_cooldown_instead_of_hammering():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return json_response({"errorCode": "wrong_password"}, status=401)

    client = make_client(handler, secret="wrong", auth_cooldown_seconds=300)
    for _ in range(3):
        with pytest.raises(SatisfactoryAuthError):
            client.query_server_state()
    assert len(calls) == 1, "later polls must be served from the cooldown, not the network"


# --- helpers -------------------------------------------------------------


@pytest.mark.parametrize(
    "secret, expected",
    [
        (TOKEN, True),
        ("eyJhIjoxfQ.DEADBEEF", True),
        ("hunter2", False),
        ("", False),
        ("no.dots-but-long-enough-to-look-tokenish", False),
        ("has.spaces in it", False),
        # Shape-only matches that must NOT be mistaken for a token: the base64
        # half does not decode to a JSON object
        ("mybase64ish.deadbeef", False),
        ("aaaaaaaaaaaa.abcdef01", False),
    ],
)
def test_api_token_detection(secret, expected):
    assert looks_like_api_token(secret) is expected


def test_fingerprint_normalisation_accepts_pasted_formats():
    assert normalize_fingerprint("AA:BB:cc:dd") == "aabbccdd"
    assert normalize_fingerprint("  aa bb CC dd  ") == "aabbccdd"
    assert format_fingerprint("aabbccdd") == "aa:bb:cc:dd"


def test_pick_returns_default_for_non_mappings():
    assert pick(None, "health", "x") == "x"
    assert pick(["a"], "health", "x") == "x"
