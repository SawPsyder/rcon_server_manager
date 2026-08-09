"""Turnstile verification must fail closed.

The property that matters is not "a good token passes" - it is that every way
the check can go wrong results in rejection, so a Cloudflare outage or a
malformed response cannot silently remove the protection.
"""

import httpx
import pytest

from app.config import get_settings
from app.services import turnstile


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SITE_KEY", "0x4AAAAAAEKfZvATLmVn5Uxb")
    monkeypatch.setenv("TURNSTILE_SECRET", "test-secret-not-a-real-one")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def disabled(monkeypatch):
    monkeypatch.delenv("TURNSTILE_SITE_KEY", raising=False)
    monkeypatch.delenv("TURNSTILE_SECRET", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _patch_post(monkeypatch, result):
    captured = {}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, data=None):
            captured["url"] = url
            captured["data"] = data
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr(turnstile.httpx, "Client", _FakeClient)
    return captured


def test_disabled_turnstile_is_a_no_op(disabled, monkeypatch):
    def _explode(*a, **kw):
        raise AssertionError("siteverify must not be called when Turnstile is off")

    monkeypatch.setattr(turnstile.httpx, "Client", _explode)
    assert turnstile.verify_turnstile("", "1.2.3.4") is True


def test_empty_token_is_rejected(configured):
    assert turnstile.verify_turnstile("", "1.2.3.4") is False


def test_success_true_passes(configured, monkeypatch):
    captured = _patch_post(monkeypatch, _FakeResponse({"success": True}))
    assert turnstile.verify_turnstile("tok", "198.51.100.7") is True
    assert captured["url"] == turnstile.SITEVERIFY_URL
    assert captured["data"]["response"] == "tok"
    assert captured["data"]["remoteip"] == "198.51.100.7"
    assert captured["data"]["secret"] == "test-secret-not-a-real-one"


def test_success_false_is_rejected(configured, monkeypatch):
    _patch_post(
        monkeypatch,
        _FakeResponse({"success": False, "error-codes": ["timeout-or-duplicate"]}),
    )
    assert turnstile.verify_turnstile("tok", "") is False


def test_network_error_fails_closed(configured, monkeypatch):
    _patch_post(monkeypatch, httpx.ConnectTimeout("no route"))
    assert turnstile.verify_turnstile("tok", "") is False


def test_non_2xx_fails_closed(configured, monkeypatch):
    _patch_post(monkeypatch, _FakeResponse({"success": True}, status_code=502))
    assert turnstile.verify_turnstile("tok", "") is False


def test_non_json_body_fails_closed(configured, monkeypatch):
    _patch_post(monkeypatch, _FakeResponse(ValueError("not json")))
    assert turnstile.verify_turnstile("tok", "") is False


def test_unexpected_shape_fails_closed(configured, monkeypatch):
    _patch_post(monkeypatch, _FakeResponse(["not", "a", "dict"]))
    assert turnstile.verify_turnstile("tok", "") is False


def test_remoteip_is_omitted_when_unknown(configured, monkeypatch):
    captured = _patch_post(monkeypatch, _FakeResponse({"success": True}))
    turnstile.verify_turnstile("tok", "")
    assert "remoteip" not in captured["data"]
