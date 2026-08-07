"""Registry of supported game server types."""

from __future__ import annotations

from app.server_types.base import (
    DefaultAdapter,
    QuickButton,
    ServerFeatures,
    ServerTypeAdapter,
    ServerTypeInfo,
)
from app.server_types.palworld import palworld_adapter
from app.server_types.sandstorm import sandstorm_adapter
from app.server_types.satisfactory import satisfactory_adapter

DEFAULT_SERVER_TYPE = "sandstorm"

_REGISTRY: dict[str, ServerTypeAdapter] = {
    sandstorm_adapter.info.id: sandstorm_adapter,
    satisfactory_adapter.info.id: satisfactory_adapter,
    palworld_adapter.info.id: palworld_adapter,
}


def list_server_types() -> list[ServerTypeInfo]:
    return [a.info for a in _REGISTRY.values()]


def list_adapters() -> list[ServerTypeAdapter]:
    return list(_REGISTRY.values())


def get_adapter(type_id: str | None) -> ServerTypeAdapter:
    key = (type_id or DEFAULT_SERVER_TYPE).strip().lower() or DEFAULT_SERVER_TYPE
    if key not in _REGISTRY:
        raise KeyError(f"Unknown server type: {type_id}")
    return _REGISTRY[key]


def is_known_type(type_id: str) -> bool:
    return (type_id or "").strip().lower() in _REGISTRY


__all__ = [
    "DEFAULT_SERVER_TYPE",
    "DefaultAdapter",
    "QuickButton",
    "ServerFeatures",
    "ServerTypeAdapter",
    "ServerTypeInfo",
    "get_adapter",
    "is_known_type",
    "list_adapters",
    "list_server_types",
]
