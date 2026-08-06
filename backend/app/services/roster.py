"""Serialize online player lists onto player-count samples."""

from __future__ import annotations

import json
from typing import Any


def roster_from_player_list(player_list: list[Any] | None) -> list[dict[str, str]]:
    """
    Compact roster for storage/API: name + optional steamid.
    Dedupes by steamid when present, else by name.
    """
    out: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for p in player_list or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").strip()
        sid = str(p.get("steamid") or p.get("steam_id") or "").strip()
        if not name and not sid:
            continue
        if sid:
            if sid in seen_ids:
                continue
            seen_ids.add(sid)
        else:
            key = name.lower()
            if key in seen_names:
                continue
            seen_names.add(key)
        entry: dict[str, str] = {"name": name or sid}
        if sid:
            entry["steamid"] = sid
        out.append(entry)
    out.sort(key=lambda e: e.get("name", "").lower())
    return out


def roster_to_json(roster: list[dict[str, str]] | None) -> str:
    try:
        return json.dumps(roster or [], ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return "[]"


def roster_from_json(raw: str | None) -> list[dict[str, str]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, str]] = []
    for item in data:
        if isinstance(item, str):
            name = item.strip()
            if name:
                out.append({"name": name})
            continue
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        sid = str(item.get("steamid") or "").strip()
        if not name and not sid:
            continue
        entry: dict[str, str] = {"name": name or sid}
        if sid:
            entry["steamid"] = sid
        out.append(entry)
    return out


def roster_names(roster: list[dict[str, str]] | None) -> list[str]:
    return [r["name"] for r in (roster or []) if r.get("name")]
