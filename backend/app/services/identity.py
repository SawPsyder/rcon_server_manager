"""Resolve platform net IDs to display names.

Steam: official Web API GetPlayerSummaries (needs STEAM_WEB_API_KEY).
Epic EOS product user ids: no public free lookup — we only show names if we
already saw them in-game (presence) or from a prior cache fill.
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


def _is_fresh(row: IdentityCache, ttl: int) -> bool:
    if not row.updated_at:
        return False
    ts = row.updated_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (_utcnow() - ts).total_seconds()
    return age < ttl and bool(row.display_name)


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


def resolve_names(
    db: Session,
    raw_ids: list[str],
    *,
    force_refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    """
    Resolve a list of raw ban/net ids to display info.

    Returns map raw_id -> {
      display_name, profile_url, avatar_url, source, steam_id?
    }
    """
    settings = get_settings()
    ttl = max(60, int(settings.identity_cache_ttl_seconds))
    out: dict[str, dict[str, Any]] = {}
    if not raw_ids:
        return out

    # Group steam ids needing API / cache
    steam_by_raw: dict[str, str] = {}
    for raw in raw_ids:
        sid = extract_steam_id(raw)
        if sid:
            steam_by_raw[raw] = sid

    steam_ids = list(dict.fromkeys(steam_by_raw.values()))
    steam_resolved = _resolve_steam_ids(db, steam_ids, ttl=ttl, force_refresh=force_refresh)

    # Presence fallback for steam ids not in API/cache
    missing_steam = [s for s in steam_ids if s not in steam_resolved or not steam_resolved[s].get("display_name")]
    if missing_steam:
        for row in (
            db.query(PlayerServerStats)
            .filter(PlayerServerStats.steam_id.in_(missing_steam))
            .all()
        ):
            name = (row.last_name or "").strip()
            if not name:
                continue
            info = {
                "display_name": name,
                "profile_url": f"https://steamcommunity.com/profiles/{row.steam_id}",
                "avatar_url": "",
                "source": "presence",
                "steam_id": row.steam_id,
            }
            steam_resolved[row.steam_id] = info
            _upsert_cache(
                db,
                platform="steam",
                external_id=row.steam_id,
                display_name=name,
                profile_url=info["profile_url"],
                avatar_url="",
                source="presence",
            )

    # EOS: only presence/cache (no public API)
    eos_by_raw: dict[str, str] = {}
    for raw in raw_ids:
        eid = extract_eos_id(raw)
        if eid:
            eos_by_raw[raw] = eid
    eos_ids = list(dict.fromkeys(eos_by_raw.values()))
    eos_resolved = _load_cached(db, "eos", eos_ids, ttl=ttl, force_refresh=force_refresh)

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
            }
        else:
            out[raw] = {
                "display_name": "",
                "profile_url": "",
                "avatar_url": "",
                "source": "",
                "steam_id": None,
            }

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to commit identity cache")

    return out


def _load_cached(
    db: Session,
    platform: str,
    external_ids: list[str],
    *,
    ttl: int,
    force_refresh: bool,
) -> dict[str, dict[str, Any]]:
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
        if force_refresh and not _is_fresh(row, ttl):
            continue
        if not force_refresh and not row.display_name:
            continue
        if not force_refresh and not _is_fresh(row, ttl) and row.source == "steam_api":
            # stale API entry still usable as soft cache if no refresh wanted
            pass
        out[row.external_id] = {
            "display_name": row.display_name or "",
            "profile_url": row.profile_url or "",
            "avatar_url": row.avatar_url or "",
            "source": row.source or "cache",
        }
    return out


def _resolve_steam_ids(
    db: Session,
    steam_ids: list[str],
    *,
    ttl: int,
    force_refresh: bool,
) -> dict[str, dict[str, Any]]:
    if not steam_ids:
        return {}

    cached = _load_cached(db, "steam", steam_ids, ttl=ttl, force_refresh=False)
    result: dict[str, dict[str, Any]] = dict(cached)

    need_api: list[str] = []
    for sid in steam_ids:
        row_info = result.get(sid)
        if force_refresh or not row_info or not row_info.get("display_name"):
            # Check DB freshness for API refresh
            need_api.append(sid)
        else:
            # have name from cache
            continue

    # Only call API for ids missing a name (or force)
    if force_refresh:
        fetch_ids = steam_ids
    else:
        fetch_ids = [
            sid
            for sid in need_api
            if not result.get(sid, {}).get("display_name")
        ]

    if not fetch_ids:
        return result

    api_key = (get_settings().steam_web_api_key or "").strip()
    if not api_key:
        logger.debug("STEAM_WEB_API_KEY not set; Steam names only from local presence cache")
        return result

    # Steam allows up to 100 ids per request
    for i in range(0, len(fetch_ids), 100):
        batch = fetch_ids[i : i + 100]
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
            avatar = str(p.get("avatarfull") or p.get("avatarmedium") or p.get("avatar") or "")
            info = {
                "display_name": name,
                "profile_url": profile,
                "avatar_url": avatar,
                "source": "steam_api",
                "steam_id": sid,
            }
            result[sid] = info
            if name:
                _upsert_cache(
                    db,
                    platform="steam",
                    external_id=sid,
                    display_name=name,
                    profile_url=profile,
                    avatar_url=avatar,
                    source="steam_api",
                )
        # be polite to the API
        if i + 100 < len(fetch_ids):
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
        data.get("response", {}).get("players")
        if isinstance(data, dict)
        else None
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
        row = IdentityCache(
            platform=platform,
            external_id=external_id,
            display_name=display_name,
            profile_url=profile_url,
            avatar_url=avatar_url,
            source=source,
            updated_at=_utcnow(),
        )
        db.add(row)
    else:
        row.display_name = display_name or row.display_name
        row.profile_url = profile_url or row.profile_url
        row.avatar_url = avatar_url or row.avatar_url
        row.source = source or row.source
        row.updated_at = _utcnow()


def steam_api_configured() -> bool:
    return bool((get_settings().steam_web_api_key or "").strip())
