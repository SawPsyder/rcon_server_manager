"""Per-server options blob (servers.options_json)."""

from __future__ import annotations

from app.services.server_options import (
    coerce_bool,
    load_options,
    merge_options,
    option_bool,
    option_str,
    save_options,
)


class Row:
    """Minimal stand-in for a Server row."""

    def __init__(self, options_json: str = "{}") -> None:
        self.options_json = options_json


def test_load_options_tolerates_broken_values():
    assert load_options(Row("")) == {}
    assert load_options(Row("not json")) == {}
    assert load_options(Row("[1,2]")) == {}, "a JSON array is not an options object"
    assert load_options(Row('{"a": 1}')) == {"a": 1}
    assert load_options(object()) == {}


def test_save_and_merge_round_trip():
    row = Row()
    save_options(row, {"verify_tls": True})
    assert load_options(row) == {"verify_tls": True}

    merged = merge_options(row, {"cert_fingerprint": "aabb"})
    assert merged == {"verify_tls": True, "cert_fingerprint": "aabb"}
    # merge must not drop untouched keys
    assert load_options(row) == merged


def test_typed_accessors():
    row = Row('{"verify_tls": "yes", "cert_fingerprint": "  AA:BB  "}')
    assert option_bool(row, "verify_tls") is True
    assert option_bool(row, "missing", True) is True
    assert option_str(row, "cert_fingerprint") == "AA:BB"
    assert option_str(row, "missing", "fallback") == "fallback"


def test_coerce_bool_accepts_stringly_typed_json():
    assert coerce_bool("true") is True
    assert coerce_bool("on") is True
    assert coerce_bool("0") is False
    assert coerce_bool("false") is False
    assert coerce_bool(None, True) is True
    assert coerce_bool(1) is True
