"""Resolve platform net IDs to display names with a durable local DB cache.

Lookup order for each id:
  1. identity_cache table (always preferred — no network if name present)
  2. PlayerServerStats.last_name (in-game names we've seen) → written to cache
  3. Steam Web API (if STEAM_WEB_API_KEY set) → written to cache

Epic EOS product user ids have no public reverse-lookup; we only store a name
if something else provides it via remember_identity().
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

# Palworld reports platform-prefixed user ids (``steam_7656…``, ``gdk_2535…``).
# The prefix is the only reliable platform signal, so it is matched *before* any
# "find 17 digits anywhere" fallback — an Xbox id that happened to be 17 digits
# would otherwise be filed as a Steam account and sent to the Steam Web API.
PLATFORM_PREFIXES: dict[str, str] = {
    "steam": "steam",
    # Microsoft: Game Pass / Microsoft Store (GDK) and Xbox console
    "gdk": "xbox",
    "xsx": "xbox",
    "xbl": "xbox",
    "psn": "psn",
    "eos": "eos",
    "mac": "mac",
}
PREFIXED_ID_RE = re.compile(r"^([A-Za-z]{2,8})_([A-Za-z0-9._-]{4,})$")

# presence keys rows on the raw net id, and PlayerServerStats.steam_id is
# VARCHAR(32). This is a storage limit, not an identity rule, so it is enforced
# by the caller that stores — parse_net_id stays purely about semantics.
MAX_NET_ID_LENGTH = 32


def parse_net_id(raw_id: str) -> tuple[str, str] | None:
    """``net id`` → ``(platform, external_id)``, or ``None`` when unrecognisable.

    The single source of truth for "is this a real player identity?", used by
    presence tracking, moderation logs and the identity cache so all three agree
    on what counts as the same person.
    """
    rid = (raw_id or "").strip()
    if not rid:
        return None

    if rid.upper().startswith("STEAMNWI:"):
        candidate = rid.split(":", 1)[1].strip()
        return ("steam", candidate) if STEAM_ID_RE.fullmatch(candidate) else None
    if rid.upper().startswith("EOS:"):
        candidate = rid[4:].strip()
        return ("eos", candidate) if candidate else None

    if STEAM_ID_RE.fullmatch(rid):
        return "steam", rid

    match = PREFIXED_ID_RE.fullmatch(rid)
    if match:
        platform = PLATFORM_PREFIXES.get(match.group(1).lower())
        if platform:
            return platform, match.group(2)
        return None

    # Last resort for Source-engine strings that embed a SteamID64
    embedded = re.search(r"(?<!\d)(\d{17})(?!\d)", rid)
    return ("steam", embedded.group(1)) if embedded else None
# Public Steam Web API (v0002 and v2 both work; v0002 is widely documented)
STEAM_SUMMARIES_URLS = (
    "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/",
    "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/",
)


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
    if not rid:
        return None
    if rid.upper().startswith("STEAMNWI:"):
        rid = rid.split(":", 1)[1].strip()
    if STEAM_ID_RE.fullmatch(rid):
        return rid
    m = re.search(r"(?<!\d)(\d{17})(?!\d)", rid)
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
    """Persist an id→name mapping (never wipe a good name with blank)."""
    external_id = (external_id or "").strip()
    display_name = (display_name or "").strip()
    if not external_id or not display_name:
        return
    platform = (platform or "unknown").strip().lower()
    # Normalize steam platform key. Only a bare SteamID64 may be *promoted* to
    # steam — a caller that already knows the platform (xbox, psn, eos) keeps it,
    # otherwise a Game Pass id would be filed as a Steam account.
    if platform in {"steamnwi", "steam_nwi"}:
        platform = "steam"
    elif platform in {"", "unknown"} and STEAM_ID_RE.fullmatch(external_id):
        platform = "steam"
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

    Returns map keyed by **each input raw_id**, and also by steamid64 / eos id
    when applicable (so callers can look up by any form).
    """
    settings = get_settings()
    ttl = max(60, int(settings.identity_cache_ttl_seconds))
    out: dict[str, dict[str, Any]] = {}
    if not raw_ids:
        return out

    # Deduplicate while preserving inputs
    unique_raw = list(dict.fromkeys([(r or "").strip() for r in raw_ids if (r or "").strip()]))

    steam_by_raw: dict[str, str] = {}
    eos_by_raw: dict[str, str] = {}
    for raw in unique_raw:
        sid = extract_steam_id(raw)
        if sid:
            steam_by_raw[raw] = sid
            continue
        eid = extract_eos_id(raw)
        if eid:
            eos_by_raw[raw] = eid

    steam_ids = list(dict.fromkeys(steam_by_raw.values()))
    eos_ids = list(dict.fromkeys(eos_by_raw.values()))

    # 1) Local DB cache — match by external_id (any steam-like platform label)
    steam_resolved = _load_cached_by_external_ids(db, steam_ids, prefer_platforms=("steam", "steamnwi"))
    eos_resolved = _load_cached_by_external_ids(db, eos_ids, prefer_platforms=("eos",))

    # 2) Presence / play history
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

    # 3) Steam Web API for still-missing (or force refresh of stale)
    fetch_ids: list[str] = []
    if force_refresh:
        for sid in steam_ids:
            info = steam_resolved.get(sid)
            if not info or not info.get("display_name"):
                fetch_ids.append(sid)
                continue
            row = (
                db.query(IdentityCache)
                .filter(IdentityCache.external_id == sid)
                .order_by(IdentityCache.updated_at.desc())
                .first()
            )
            age = _age_seconds(row) if row else None
            if age is None or age >= ttl:
                fetch_ids.append(sid)
    else:
        fetch_ids = [
            sid for sid in steam_ids if not (steam_resolved.get(sid) or {}).get("display_name")
        ]

    if fetch_ids:
        logger.info(
            "Steam name lookup: %s id(s) missing from cache, API configured=%s",
            len(fetch_ids),
            steam_api_configured(),
        )
        api_hits = _fetch_steam_api(db, fetch_ids)
        steam_resolved.update(api_hits)
        logger.info("Steam name lookup: resolved %s/%s via API", len(api_hits), len(fetch_ids))

    def _pack_steam(sid: str, info: dict[str, Any]) -> dict[str, Any]:
        return {
            "display_name": info.get("display_name") or "",
            "profile_url": info.get("profile_url")
            or f"https://steamcommunity.com/profiles/{sid}",
            "avatar_url": info.get("avatar_url") or "",
            "source": info.get("source") or "",
            "steam_id": sid,
            "cached": bool(info.get("cached")),
        }

    for raw in unique_raw:
        if raw in steam_by_raw:
            sid = steam_by_raw[raw]
            out[raw] = _pack_steam(sid, steam_resolved.get(sid) or {})
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

    # Secondary keys: allow lookup by pure steamid / eos id
    for sid, info in steam_resolved.items():
        if sid not in out:
            out[sid] = _pack_steam(sid, info)
    for eid, info in eos_resolved.items():
        key = f"EOS:{eid}"
        if key not in out:
            out[key] = {
                "display_name": info.get("display_name") or "",
                "profile_url": info.get("profile_url") or "",
                "avatar_url": info.get("avatar_url") or "",
                "source": info.get("source") or "",
                "steam_id": None,
                "cached": bool(info.get("cached")),
            }
        if eid not in out:
            out[eid] = out[key]

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to commit identity cache")

    return out


def _load_cached_by_external_ids(
    db: Session,
    external_ids: list[str],
    *,
    prefer_platforms: tuple[str, ...] = (),
) -> dict[str, dict[str, Any]]:
    """Load cache rows by external_id (platform-flexible)."""
    if not external_ids:
        return {}
    rows = (
        db.query(IdentityCache)
        .filter(IdentityCache.external_id.in_(external_ids))
        .all()
    )
    # Prefer certain platforms if multiple rows exist for same id
    by_id: dict[str, IdentityCache] = {}
    for row in rows:
        name = (row.display_name or "").strip()
        if not name:
            continue
        existing = by_id.get(row.external_id)
        if existing is None:
            by_id[row.external_id] = row
            continue
        # Prefer preferred platforms / steam_api source
        pref = {p.lower() for p in prefer_platforms}
        if row.platform.lower() in pref and existing.platform.lower() not in pref:
            by_id[row.external_id] = row
        elif row.source == "steam_api" and existing.source != "steam_api":
            by_id[row.external_id] = row

    out: dict[str, dict[str, Any]] = {}
    for eid, row in by_id.items():
        out[eid] = {
            "display_name": (row.display_name or "").strip(),
            "profile_url": row.profile_url or "",
            "avatar_url": row.avatar_url or "",
            "source": row.source or "cache",
            "cached": True,
        }
    return out


def _normalize_api_key(raw: str) -> str:
    key = (raw or "").strip().strip('"').strip("'")
    # Some people paste "Key: XXXXX"
    if key.lower().startswith("key:"):
        key = key.split(":", 1)[1].strip()
    return key


def _fetch_steam_api(db: Session, steam_ids: list[str]) -> dict[str, dict[str, Any]]:
    settings = get_settings()
    api_key = _normalize_api_key(settings.resolved_steam_api_key())
    if not api_key:
        logger.info("STEAM_WEB_API_KEY not set; Steam names only from local cache/presence")
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
            sid = str(p.get("steamid") or "").strip()
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
    last_err: Exception | None = None
    with httpx.Client(timeout=12.0, follow_redirects=True) as client:
        for url in STEAM_SUMMARIES_URLS:
            try:
                resp = client.get(url, params=params)
                if resp.status_code == 403:
                    logger.warning(
                        "Steam Web API returned 403 (invalid/forbidden key?) for %s", url
                    )
                    last_err = httpx.HTTPStatusError(
                        "403", request=resp.request, response=resp
                    )
                    continue
                resp.raise_for_status()
                data = resp.json()
                players = (
                    data.get("response", {}).get("players")
                    if isinstance(data, dict)
                    else None
                )
                return list(players or [])
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.debug("Steam API attempt failed (%s): %s", url, exc)
                continue
    if last_err:
        raise last_err
    return []


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

    if display_name:
        row.display_name = display_name
    if profile_url:
        row.profile_url = profile_url
    if avatar_url:
        row.avatar_url = avatar_url
    if source:
        if source == "steam_api" or row.source != "steam_api":
            row.source = source
    row.updated_at = _utcnow()


def steam_api_configured() -> bool:
    s = get_settings()
    return bool(_normalize_api_key(s.steam_web_api_key or s.steam_api_key))
