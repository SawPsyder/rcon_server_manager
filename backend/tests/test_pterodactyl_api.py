"""Pterodactyl Client API transport, driven through httpx.MockTransport.

Nothing here touches a socket or needs a panel. Response shapes are taken from
pterodactyl/panel v1.15.0's transformers, so a change upstream shows up as a
failure here rather than as a mystery in production.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.pterodactyl_api import (
    PanelClient,
    PterodactylApiError,
    PterodactylAuthError,
    PterodactylConflictError,
    PterodactylNotFoundError,
    PterodactylRateLimitError,
    PterodactylTimeoutError,
    describe_failure,
    panel_registry,
    percent_of,
)
from app.services.pterodactyl_settings import PterodactylConfig

UUID = "d3aac109-e5e0-4331-b03e-3454f7e136dc"
KEY = "ptlc_abcdefghijklmnopqrstuvwxyz012345"


def config(**overrides) -> PterodactylConfig:
    params = {"base_url": "https://panel.example.com", "api_key": KEY, "verify_tls": True}
    params.update(overrides)
    return PterodactylConfig(**params)


def make_client(handler, **kwargs) -> PanelClient:
    return PanelClient(config(), transport=httpx.MockTransport(handler), **kwargs)


def recorder(payload=None, *, status: int = 200, text: str | None = None, headers=None):
    """Handler that records requests and answers with a fixed response."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if text is not None:
            return httpx.Response(status, text=text, headers=headers or {})
        return httpx.Response(status, json=payload, headers=headers or {})

    return handler, seen


def server_payload(**overrides):
    attrs = {
        "server_owner": True,
        "identifier": "d3aac109",
        "uuid": UUID,
        "name": "Sandstorm #1",
        "node": "node-01",
        "status": None,
        "is_suspended": False,
        "limits": {"memory": 4096, "swap": 0, "disk": 20480, "io": 500, "cpu": 200},
    }
    attrs.update(overrides)
    return {"object": "server", "attributes": attrs}


def resources_payload(**overrides):
    res = {
        "memory_bytes": 671088640,
        "cpu_absolute": 152.2,
        "disk_bytes": 2147483648,
        "network_rx_bytes": 676237,
        "network_tx_bytes": 1097738,
        "uptime": 3600000,
    }
    res.update(overrides)
    return {
        "object": "stats",
        "attributes": {
            "current_state": "running",
            "is_suspended": False,
            "resources": res,
        },
    }


def errors_payload(code: str, status: str, detail: str):
    return {"errors": [{"code": code, "status": status, "detail": detail}]}


# --- request shape ---------------------------------------------------------


def test_sends_bearer_token_and_json_accept():
    handler, seen = recorder(server_payload())
    make_client(handler).get_server(UUID)
    assert seen[0].headers["Authorization"] == f"Bearer {KEY}"
    assert seen[0].headers["Accept"] == "application/json"


def test_post_sets_content_type_so_laravel_parses_the_body():
    handler, seen = recorder(status=204)
    make_client(handler).send_power(UUID, "restart")
    assert seen[0].method == "POST"
    assert seen[0].headers["Content-Type"] == "application/json"
    assert json.loads(seen[0].content.decode()) == {"signal": "restart"}


def test_urls_never_double_slash_or_trail():
    handler, seen = recorder(resources_payload())
    client = PanelClient(
        config(base_url="https://panel.example.com"),
        transport=httpx.MockTransport(handler),
    )
    client.get_resources(UUID)
    assert str(seen[0].url) == (
        f"https://panel.example.com/api/client/servers/{UUID}/resources"
    )


def test_rejects_an_unknown_power_signal_without_calling_out():
    handler, seen = recorder(status=204)
    with pytest.raises(PterodactylApiError):
        make_client(handler).send_power(UUID, "reboot")
    assert seen == []


# --- parsing ---------------------------------------------------------------


def test_resources_are_parsed_from_the_real_shape():
    handler, _ = recorder(resources_payload())
    res = make_client(handler).get_resources(UUID)
    assert res.current_state == "running"
    assert res.memory_bytes == 671088640
    assert res.cpu_absolute == pytest.approx(152.2)
    assert res.network_rx_bytes == 676237
    # uptime is milliseconds upstream and stays that way
    assert res.uptime_ms == 3600000


def test_null_status_reads_as_healthy_and_a_real_one_is_kept():
    handler, _ = recorder(server_payload(status=None))
    assert make_client(handler).get_server(UUID).status == ""

    handler, _ = recorder(server_payload(status="installing"))
    assert make_client(handler).get_server(UUID).status == "installing"


def test_limits_are_read_in_mib():
    handler, _ = recorder(server_payload())
    server = make_client(handler).get_server(UUID)
    assert server.memory_limit_mb == 4096
    assert server.disk_limit_mb == 20480
    assert server.cpu_limit == 200


def test_pagination_walks_every_page():
    pages = {
        "1": {
            "data": [server_payload()],
            "meta": {"pagination": {"total_pages": 3, "current_page": 1}},
        },
        "2": {
            "data": [server_payload(uuid="u2", identifier="i2", name="Two")],
            "meta": {"pagination": {"total_pages": 3, "current_page": 2}},
        },
        "3": {
            "data": [server_payload(uuid="u3", identifier="i3", name="Three")],
            "meta": {"pagination": {"total_pages": 3, "current_page": 3}},
        },
    }
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        seen.append(page)
        return httpx.Response(200, json=pages[page])

    servers = make_client(handler).list_servers()
    assert seen == ["1", "2", "3"]
    assert [s.name for s in servers] == ["Sandstorm #1", "Two", "Three"]


# --- unlimited limits ------------------------------------------------------


@pytest.mark.parametrize("limit", [0, None, -1])
def test_percent_of_returns_none_for_unlimited(limit):
    """The panel encodes unlimited as 0 on memory, disk and CPU alike."""
    assert percent_of(1234, limit) is None


def test_percent_of_computes_normally():
    assert percent_of(50, 200) == pytest.approx(25.0)


def test_zero_limits_do_not_raise():
    handler, _ = recorder(server_payload(limits={"memory": 0, "disk": 0, "cpu": 0}))
    server = make_client(handler).get_server(UUID)
    assert (server.memory_limit_mb, server.disk_limit_mb, server.cpu_limit) == (0, 0, 0)
    assert percent_of(999, server.memory_limit_mb) is None


# --- errors ----------------------------------------------------------------


def test_401_raises_auth_error_and_stops_retrying():
    handler, seen = recorder(
        errors_payload("InvalidCredentials", "401", "Unauthenticated."), status=401
    )
    client = make_client(handler)

    with pytest.raises(PterodactylAuthError):
        client.get_resources(UUID)
    # The cooldown must short-circuit before a second upstream request: a bad
    # key times every linked server times every tab would burn the rate limit.
    with pytest.raises(PterodactylAuthError):
        client.get_resources(UUID)
    assert len(seen) == 1


def test_403_does_not_cool_down_because_it_can_be_per_server():
    handler, seen = recorder(
        errors_payload("Forbidden", "403", "This action is unauthorized."), status=403
    )
    client = make_client(handler)
    for _ in range(2):
        with pytest.raises(PterodactylAuthError):
            client.get_resources(UUID)
    assert len(seen) == 2


def test_404_message_does_not_assert_the_server_is_gone():
    handler, _ = recorder(errors_payload("NotFoundHttpException", "404", ""), status=404)
    with pytest.raises(PterodactylNotFoundError) as exc:
        make_client(handler).get_resources(UUID)
    message = str(exc.value).lower()
    assert "cannot see" in message or "no such server" in message


def test_409_surfaces_the_panels_own_sentence():
    detail = "This server is currently suspended and the functionality requested is unavailable."
    handler, _ = recorder(
        errors_payload("ServerStateConflictException", "409", detail), status=409
    )
    with pytest.raises(PterodactylConflictError) as exc:
        make_client(handler).send_power(UUID, "start")
    assert detail in str(exc.value)


def test_429_backs_off_until_retry_after_elapses():
    handler, seen = recorder(status=429, payload={}, headers={"Retry-After": "30"})
    client = make_client(handler)

    with pytest.raises(PterodactylRateLimitError):
        client.get_resources(UUID)
    with pytest.raises(PterodactylRateLimitError):
        client.get_resources(UUID)
    assert len(seen) == 1


def test_html_response_says_it_is_not_a_panel():
    handler, _ = recorder(text="<!doctype html><html><body>Login</body></html>")
    with pytest.raises(PterodactylApiError) as exc:
        make_client(handler).get_resources(UUID)
    assert exc.value.code == "not_a_panel"


def test_timeout_maps_to_the_timeout_class():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(PterodactylTimeoutError):
        make_client(handler).get_resources(UUID)


def test_tls_failure_points_at_the_verify_tls_checkbox():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", request=request
        )

    with pytest.raises(PterodactylApiError) as exc:
        make_client(handler).get_resources(UUID)
    message = str(exc.value)
    assert "Verify the panel's TLS certificate" in message
    assert "uncheck" in message.lower()


def test_plain_connect_failure_points_at_the_url():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Name or service not known", request=request)

    with pytest.raises(PterodactylApiError) as exc:
        make_client(handler).get_resources(UUID)
    assert "panel URL" in str(exc.value)


# --- caching ---------------------------------------------------------------


def test_resources_are_cached_so_extra_tabs_are_free():
    handler, seen = recorder(resources_payload())
    client = make_client(handler)
    client.get_resources(UUID)
    client.get_resources(UUID)
    client.get_resources(UUID)
    assert len(seen) == 1


def test_an_expired_cache_entry_refetches():
    handler, seen = recorder(resources_payload())
    client = make_client(handler, resource_ttl=0.0)
    client.get_resources(UUID)
    client.get_resources(UUID)
    assert len(seen) == 2


def test_power_invalidates_only_that_servers_cache():
    other = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(204)
        return httpx.Response(200, json=resources_payload())

    client = make_client(handler)
    client.get_resources(UUID)
    client.get_resources(other)
    assert len(seen) == 2

    client.send_power(UUID, "restart")
    client.get_resources(other)  # still cached
    client.get_resources(UUID)  # refetched
    assert len([r for r in seen if r.method == "GET"]) == 3


def test_server_object_uses_its_own_longer_cache():
    handler, seen = recorder(server_payload())
    client = make_client(handler)
    client.get_server(UUID)
    client.get_server(UUID)
    assert len(seen) == 1


# --- describe_failure ------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_registry():
    yield
    panel_registry.invalidate_all()


def test_describe_failure_asks_for_a_url_first():
    assert "panel URL" in describe_failure(config(base_url=""))


def test_describe_failure_asks_for_a_key():
    assert "API key" in describe_failure(config(api_key=""))


def _describe_with(monkeypatch, handler) -> str:
    cfg = config()
    client = PanelClient(cfg, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        "app.services.pterodactyl_api.client_for", lambda _cfg: client
    )
    return describe_failure(cfg)


def test_describe_failure_is_empty_on_success(monkeypatch):
    handler, _ = recorder({"object": "list", "data": [], "meta": {}})
    assert _describe_with(monkeypatch, handler) == ""


def test_describe_failure_names_the_application_key_mistake(monkeypatch):
    # The panel's own 401 detail is the bare word "Unauthenticated.", which
    # must not be allowed to replace the actionable sentence.
    handler, _ = recorder(
        status=401, payload=errors_payload("InvalidCredentials", "401", "Unauthenticated.")
    )
    message = _describe_with(monkeypatch, handler)
    assert "Client API key" in message and "Application" in message


def test_describe_failure_flags_a_non_panel_url(monkeypatch):
    handler, _ = recorder({"totally": "different"})
    message = _describe_with(monkeypatch, handler)
    assert "not like a Pterodactyl panel" in message


def test_describe_failure_clears_a_stale_cooldown(monkeypatch):
    """Pressing Test after fixing something must retry, not replay the cooldown."""
    responses = [httpx.Response(401, json={}), httpx.Response(200, json={"data": []})]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    cfg = config()
    client = PanelClient(cfg, transport=httpx.MockTransport(handler))
    monkeypatch.setattr("app.services.pterodactyl_api.client_for", lambda _cfg: client)

    assert describe_failure(cfg) != ""  # first probe fails and sets the cooldown
    assert describe_failure(cfg) == ""  # second probe still reaches the panel
