"""Resolve platform net IDs to display names with a durable local DB cache.

Lookup order for each id:
  1. identity_cache table (always preferred — no network if name present)
  2. PlayerServerStats.last_name (in-game names we've seen) → written to cache
  3. Steam Web API (if STEAM_WEB_API_KEY set) → written to cache

Epic EOS product user ids have no public reverse-lookup; we only store a name
if something else provides it (future sources) via remember_identity().

force_refresh=True only re-queries Steam for rows older than IDENTITY_CACHE_TTL.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import IdentityCache, PlayerServerStats

logger = logging.getLogger(__name__)

STEAM_ID_RE = re.compile(r"^\d{17}$")
STEAM_SUMMARIES_URL = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _age_seconds(row: IdentityCache) -> float | None:
    if not row.updated_at:
        return None
    ts = row.updated_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (_utcnow() - ts).total_seconds())


def extract_steam_id(raw_id: str) -> str | None:
    rid = (raw_id or "").strip()
    if rid.upper().startswith("STEAMNWI:"):
        rid = rid.split(":", 1)[1].strip()
    if STEAM_ID_RE.fullmatch(rid):
        return rid
    m = re.search(r"(\d{17})", rid)
    return m.group(1) if m else None


def extract_eos_id(raw_id: str) -> str | None:
    rid = (raw_id or "").strip()
    if rid.upper().startswith("EOS:"):
        return rid[4:].strip() or None
    return None


def remember_identity(
    db: Session,
    *,
    platform: str,
    external_id: str,
    display_name: str,
    profile_url: str = "",
    avatar_url: str = "",
    source: str = "manual",
    commit: bool = False,
) -> None:
    """
    Persist an id→name mapping. Prefer overwriting empty names; do not wipe a
    better API name with a blank value.
    """
    external_id = (external_id or "").strip()
    display_name = (display_name or "").strip()
    if not external_id or not display_name:
        return
    platform = (platform or "unknown").strip().lower()
    _upsert_cache(
        db,
        platform=platform,
        external_id=external_id,
        display_name=display_name,
        profile_url=profile_url,
        avatar_url=avatar_url,
        source=source,
    )
    if commit:
        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to commit identity cache")


def resolve_names(
    db: Session,
    raw_ids: list[str],
    *,
    force_refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    """
    Resolve raw ban/net ids using the local cache first.

    Returns map raw_id -> {
      display_name, profile_url, avatar_url, source, steam_id?, cached
    }
    """
    settings = get_settings()
    ttl = max(60, int(settings.identity_cache_ttl_seconds))
    out: dict[str, dict[str, Any]] = {}
    if not raw_ids:
        return out

    steam_by_raw: dict[str, str] = {}
    eos_by_raw: dict[str, str] = {}
    for raw in raw_ids:
        sid = extract_steam_id(raw)
        if sid:
            steam_by_raw[raw] = sid
            continue
        eid = extract_eos_id(raw)
        if eid:
            eos_by_raw[raw] = eid

    steam_ids = list(dict.fromkeys(steam_by_raw.values()))
    eos_ids = list(dict.fromkeys(eos_by_raw.values()))

    # 1) Local DB cache (authoritative unless force_refresh + stale)
    steam_resolved = _load_cached_names(db, "steam", steam_ids)
    eos_resolved = _load_cached_names(db, "eos", eos_ids)

    # 2) Presence / play history → fill cache for missing steam names
    missing_steam = [s for s in steam_ids if not (steam_resolved.get(s) or {}).get("display_name")]
    if missing_steam:
        for row in (
            db.query(PlayerServerStats)
            .filter(PlayerServerStats.steam_id.in_(missing_steam))
            .all()
        ):
            name = (row.last_name or "").strip()
            if not name or name == row.steam_id:
                continue
            profile = f"https://steamcommunity.com/profiles/{row.steam_id}"
            steam_resolved[row.steam_id] = {
                "display_name": name,
                "profile_url": profile,
                "avatar_url": "",
                "source": "presence",
                "cached": False,
            }
            remember_identity(
                db,
                platform="steam",
                external_id=row.steam_id,
                display_name=name,
                profile_url=profile,
                source="presence",
            )

    # 3) Steam API only for ids still missing a name (or force refresh of stale)
    fetch_ids: list[str] = []
    if force_refresh:
        for sid in steam_ids:
            info = steam_resolved.get(sid)
            if not info or not info.get("display_name"):
                fetch_ids.append(sid)
                continue
            # Only re-fetch if cached entry is older than TTL
            row = (
                db.query(IdentityCache)
                .filter(
                    IdentityCache.platform == "steam",
                    IdentityCache.external_id == sid,
                )
                .first()
            )
            age = _age_seconds(row) if row else None
            if age is None or age >= ttl:
                fetch_ids.append(sid)
    else:
        fetch_ids = [
            sid
            for sid in steam_ids
            if not (steam_resolved.get(sid) or {}).get("display_name")
        ]

    if fetch_ids:
        api_hits = _fetch_steam_api(db, fetch_ids)
        steam_resolved.update(api_hits)

    for raw in raw_ids:
        if raw in steam_by_raw:
            sid = steam_by_raw[raw]
            info = steam_resolved.get(sid) or {}
            out[raw] = {
                "display_name": info.get("display_name") or "",
                "profile_url": info.get("profile_url")
                or f"https://steamcommunity.com/profiles/{sid}",
                "avatar_url": info.get("avatar_url") or "",
                "source": info.get("source") or "",
                "steam_id": sid,
                "cached": bool(info.get("cached")),
            }
        elif raw in eos_by_raw:
            eid = eos_by_raw[raw]
            info = eos_resolved.get(eid) or {}
            out[raw] = {
                "display_name": info.get("display_name") or "",
                "profile_url": info.get("profile_url") or "",
                "avatar_url": info.get("avatar_url") or "",
                "source": info.get("source") or "",
                "steam_id": None,
                "cached": bool(info.get("cached")),
            }
        else:
            out[raw] = {
                "display_name": "",
                "profile_url": "",
                "avatar_url": "",
                "source": "",
                "steam_id": None,
                "cached": False,
            }

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to commit identity cache")

    return out


def _load_cached_names(
    db: Session,
    platform: str,
    external_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Return all cached rows that have a display_name (never expires for reads)."""
    if not external_ids:
        return {}
    rows = (
        db.query(IdentityCache)
        .filter(
            IdentityCache.platform == platform,
            IdentityCache.external_id.in_(external_ids),
        )
        .all()
    )
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = (row.display_name or "").strip()
        if not name:
            continue
        out[row.external_id] = {
            "display_name": name,
            "profile_url": row.profile_url or "",
            "avatar_url": row.avatar_url or "",
            "source": row.source or "cache",
            "cached": True,
        }
    return out


def _fetch_steam_api(db: Session, steam_ids: list[str]) -> dict[str, dict[str, Any]]:
    api_key = (get_settings().steam_web_api_key or "").strip()
    if not api_key:
        logger.debug("STEAM_WEB_API_KEY not set; skipping remote Steam lookup")
        return {}

    result: dict[str, dict[str, Any]] = {}
    for i in range(0, len(steam_ids), 100):
        batch = steam_ids[i : i + 100]
        try:
            players = _steam_get_player_summaries(api_key, batch)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Steam Web API lookup failed: %s", exc)
            break
        for p in players:
            sid = str(p.get("steamid") or "")
            if not sid:
                continue
            name = str(p.get("personaname") or "").strip()
            profile = str(p.get("profileurl") or f"https://steamcommunity.com/profiles/{sid}")
            avatar = str(
                p.get("avatarfull") or p.get("avatarmedium") or p.get("avatar") or ""
            )
            if not name:
                continue
            result[sid] = {
                "display_name": name,
                "profile_url": profile,
                "avatar_url": avatar,
                "source": "steam_api",
                "cached": False,
            }
            # Always persist so next request is free
            remember_identity(
                db,
                platform="steam",
                external_id=sid,
                display_name=name,
                profile_url=profile,
                avatar_url=avatar,
                source="steam_api",
            )
        if i + 100 < len(steam_ids):
            time.sleep(0.05)
    return result


def _steam_get_player_summaries(api_key: str, steam_ids: list[str]) -> list[dict[str, Any]]:
    params = {
        "key": api_key,
        "steamids": ",".join(steam_ids),
    }
    with httpx.Client(timeout=8.0) as client:
        resp = client.get(STEAM_SUMMARIES_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
    players = (
        data.get("response", {}).get("players") if isinstance(data, dict) else None
    )
    return list(players or [])


def _upsert_cache(
    db: Session,
    *,
    platform: str,
    external_id: str,
    display_name: str,
    profile_url: str,
    avatar_url: str,
    source: str,
) -> None:
    row = (
        db.query(IdentityCache)
        .filter(
            IdentityCache.platform == platform,
            IdentityCache.external_id == external_id,
        )
        .first()
    )
    if row is None:
        db.add(
            IdentityCache(
                platform=platform,
                external_id=external_id,
                display_name=display_name,
                profile_url=profile_url or "",
                avatar_url=avatar_url or "",
                source=source or "",
                updated_at=_utcnow(),
            )
        )
        return

    # Prefer non-empty updates; keep richer avatar/profile when new ones are blank
    if display_name:
        row.display_name = display_name
    if profile_url:
        row.profile_url = profile_url
    if avatar_url:
        row.avatar_url = avatar_url
    if source:
        # Prefer steam_api over presence once we have API data
        if source == "steam_api" or row.source != "steam_api":
            row.source = source
    row.updated_at = _utcnow()


def steam_api_configured() -> bool:
    return bool((get_settings().steam_web_api_key or "").strip())
