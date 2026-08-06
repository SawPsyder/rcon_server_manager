"""Insurgency: Sandstorm server type adapter."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.server_types.base import QuickButton, ServerFeatures, ServerTypeInfo
from app.services.query import QueryError, SourceQuery
from app.services.rcon import RconError

logger = logging.getLogger(__name__)

# Real humans use SteamNWI:<17 digits> (or bare 17-digit SteamID64)
HUMAN_NETID_RE = re.compile(r"(?:SteamNWI:)?(\d{17})", re.IGNORECASE)
PLAYER_ROW_RE = re.compile(
    r"(?P<id>\d+)\s*\|\s*"
    r"(?P<name>[^|]+?)\s*\|\s*"
    r"(?P<netid>SteamNWI:\d{17}|None:INVALID|\d{17}|[^|]+?)\s*\|\s*"
    r"(?P<ip>[^|]*)\s*\|\s*"
    r"(?P<score>-?\d+)",
    re.IGNORECASE,
)

ALLOWED_COMMAND_PREFIXES = (
    "gamever",
    "quit",
    "exit",
    "help",
    "listplayers",
    "kick",
    "permban",
    "travel",
    "ban",
    "banid",
    "listbans",
    "unban",
    "say",
    "restartround",
    "maps",
    "scenarios",
    "travelscenario",
    "gamemodeproperty",
    "listgamemodeproperties",
)

GAMEMODE_KEYS: dict[str, str] = {
    "checkpoint": "checkpoint",
    "checkpoint_ins": "checkpoint_ins",
    "checkpointhardcore": "checkpointhardcore",
    "checkpointhardcore_ins": "checkpointhardcore_ins",
    "domination": "domination",
    "firefight_east": "firefight_east",
    "firefight_west": "firefight_west",
    "frontline": "frontline",
    "outpost": "outpost",
    "push": "push",
    "push_ins": "push_ins",
    "skirmish": "skirmish",
    "teamdeathmatch": "teamdeathmatch",
    "survival": "survival",
    "ambush": "ambush",
}

GAMEMODE_LABELS: dict[str, str] = {
    "checkpoint": "CheckPoint Security",
    "checkpoint_ins": "CheckPoint Insurgents",
    "checkpointhardcore": "CheckPoint HC Security",
    "checkpointhardcore_ins": "CheckPoint HC Insurgents",
    "domination": "Domination",
    "firefight_east": "Firefight East",
    "firefight_west": "Firefight West",
    "frontline": "Frontline",
    "outpost": "Outpost",
    "push": "Push Security",
    "push_ins": "Push Insurgents",
    "skirmish": "Skirmish",
    "teamdeathmatch": "TeamDeathMatch",
    "survival": "Survival",
    "ambush": "Ambush",
}

# Hardcoded dashboard shortcuts (not user-editable)
QUICK_BUTTONS = (
    QuickButton("List Players", "listplayers"),
    QuickButton("List Bans", "listbans"),
    QuickButton("List Maps", "maps"),
    QuickButton("Restart Round", "restartround 0"),
)

DEFAULT_PREFERRED_GAMEMODE = "checkpoint"

# listbans entries are often concatenated without newlines, e.g.
# 7656… Permanent (reason)SteamNWI:7656… Permanent (reason)EOS:… Permanent (reason)
BAN_ENTRY_RE = re.compile(
    r"(?P<raw_id>"
    r"SteamNWI:\d{17}"
    r"|EOS:[0-9a-fA-F|]+"
    r"|\d{17}"
    r")\s+"
    r"(?P<duration>"
    r"Permanent"
    r"|Temporary"
    r"|\d+\s*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)?"
    r")"
    r"\s*"
    r"(?:\((?P<reason>[^)]*)\))?",
    re.IGNORECASE,
)


def parse_listbans(text: str) -> list[dict[str, Any]]:
    """Parse Sandstorm RCON listbans into structured ban records."""
    if not text:
        return []
    raw = text.replace("\r\n", "\n").replace("\r", "\n")
    # Drop obvious command echo / headers
    cleaned_lines: list[str] = []
    for line in raw.split("\n"):
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if low in {"listbans", "bans", "id", "banned"}:
            continue
        cleaned_lines.append(s)
    blob = " ".join(cleaned_lines) if cleaned_lines else raw

    bans: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, m in enumerate(BAN_ENTRY_RE.finditer(blob), start=1):
        raw_id = m.group("raw_id").strip()
        if raw_id in seen:
            continue
        seen.add(raw_id)
        duration = (m.group("duration") or "").strip()
        reason = (m.group("reason") or "").strip()
        platform, net_id, display_id = _classify_ban_id(raw_id)
        bans.append(
            {
                "index": i,
                "platform": platform,
                "raw_id": raw_id,
                "net_id": net_id,
                "display_id": display_id,
                "duration": duration or "—",
                "reason": reason or "—",
                "permanent": duration.lower() == "permanent" if duration else False,
            }
        )
    return bans


def _classify_ban_id(raw_id: str) -> tuple[str, str, str]:
    """
    Return (platform, net_id_for_unban, display_id).
    unban typically wants the id as listbans shows it (quoted).
    """
    rid = raw_id.strip()
    if rid.upper().startswith("STEAMNWI:"):
        steam = rid.split(":", 1)[1]
        return "Steam (NWI)", rid, steam
    if rid.upper().startswith("EOS:"):
        return "Epic (EOS)", rid, rid[4:]
    if re.fullmatch(r"\d{17}", rid):
        return "Steam", rid, rid
    return "Unknown", rid, rid


def _is_human_netid(netid: str) -> bool:
    n = (netid or "").strip()
    if not n or n.upper() in {"NONE:INVALID", "INVALID", "NONE", "BOT"}:
        return False
    return HUMAN_NETID_RE.search(n) is not None


def _steam_from_netid(netid: str) -> str:
    m = HUMAN_NETID_RE.search(netid or "")
    return m.group(1) if m else ""


def parse_listplayers(text: str) -> list[dict[str, Any]]:
    """Parse RCON listplayers; return human players only."""
    if not text:
        return []

    raw = text.replace("\r\n", "\n").replace("\t", " ")
    players: list[dict[str, Any]] = []
    seen_steam: set[str] = set()

    for m in PLAYER_ROW_RE.finditer(raw):
        name = m.group("name").strip().strip('"')
        netid = m.group("netid").strip()
        if name.lower() == "name" or netid.lower() in {"netid", "id"}:
            continue
        if not _is_human_netid(netid):
            continue
        steam = _steam_from_netid(netid)
        if steam and steam in seen_steam:
            continue
        if steam:
            seen_steam.add(steam)
        try:
            score = int(m.group("score"))
        except ValueError:
            score = 0
        try:
            row_id = int(m.group("id"))
        except ValueError:
            row_id = len(players) + 1
        players.append(
            {
                "id": len(players) + 1,
                "server_id": row_id,
                "name": name,
                "steamid": steam or netid,
                "ip": m.group("ip").strip(),
                "score": score,
                "duration": 0.0,
                "duration_pretty": "00:00:00",
            }
        )

    if players:
        return players

    for j, sid in enumerate(dict.fromkeys(HUMAN_NETID_RE.findall(raw)), start=1):
        players.append(
            {
                "id": j,
                "server_id": j,
                "name": f"Player {j}",
                "steamid": sid,
                "ip": "",
                "score": 0,
                "duration": 0.0,
                "duration_pretty": "00:00:00",
            }
        )
    return players


def listplayers_via_rcon(
    host: str,
    rcon_port: int,
    password: str,
    timeout: float = 5.0,
) -> list[dict[str, Any]]:
    if not password:
        raise RconError("No RCON password")
    # Use persistent pool — never open a new TCP session per poll
    from app.services.rcon import run_rcon

    raw = run_rcon(host, rcon_port, password, "listplayers", timeout=timeout, persistent=True)
    return parse_listplayers(raw or "")


def map_gamemodes(map_row: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in GAMEMODE_KEYS:
        val = getattr(map_row, key, "") or ""
        if str(val).strip():
            out[key] = str(val).strip()
    return out


def map_lightings(map_row: Any) -> list[str]:
    lights: list[str] = []
    if getattr(map_row, "day", True):
        lights.append("Day")
    if getattr(map_row, "night", True):
        lights.append("Night")
    for extra in ("globalday", "dusk", "dawn", "dark", "fog", "rain", "winter"):
        if getattr(map_row, extra, False):
            lights.append(extra.capitalize() if extra != "globalday" else "GlobalDay")
    seen: set[str] = set()
    result: list[str] = []
    for item in lights:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result or ["Day"]


def build_travel_command(
    map_name: str,
    scenario: str,
    lighting: str,
    gamemode_key: str,
) -> str:
    game_param = gamemode_key
    if gamemode_key == "checkpointhardcore_ins":
        game_param = "checkpointhardcore"
    return f"travel {map_name}?Scenario={scenario}?Lighting={lighting}?game={game_param}"


class SandstormAdapter:
    info = ServerTypeInfo(
        id="sandstorm",
        label="Insurgency: Sandstorm",
        default_query_port=27131,
        default_rcon_port=27015,
        features=ServerFeatures(
            map_travel=True,
            structured_player_list=True,
            kick_ban=True,
            admin_say=True,
            a2s_query=True,
        ),
        quick_buttons=QUICK_BUTTONS,
    )
    allowed_rcon_prefixes = ALLOWED_COMMAND_PREFIXES

    def is_command_allowed(self, command: str) -> bool:
        cmd = command.strip().lower()
        if not cmd:
            return False
        first = cmd.split()[0]
        for prefix in self.allowed_rcon_prefixes:
            if first == prefix or first.startswith(prefix):
                return True
        return False

    def sample_players(
        self,
        host: str,
        query_port: int,
        rcon_port: int | None = None,
        rcon_password: str = "",
        timeout: float = 3.0,
    ) -> dict[str, Any]:
        """
        Priority:
          1) RCON listplayers humans (A2S often reports 0 on Sandstorm)
          2) max(A2S_INFO Players, A2S player list length)
        """
        a2s_players = 0
        a2s_max = 0
        a2s_bots = 0
        a2s_list: list[dict[str, Any]] = []
        online = False
        a2s_error: str | None = None
        source = "none"

        try:
            with SourceQuery(host, query_port, timeout=timeout) as q:
                info = q.get_info()
                online = True
                a2s_players = int(info.get("Players") or 0)
                a2s_max = int(info.get("MaxPlayers") or 0)
                a2s_bots = int(info.get("Bots") or 0)
                try:
                    a2s_list = q.get_players() or []
                except QueryError:
                    a2s_list = []
        except Exception as exc:  # noqa: BLE001
            a2s_error = str(exc)
            online = False

        rcon_list: list[dict[str, Any]] = []
        rcon_error: str | None = None
        if rcon_password and rcon_port:
            try:
                rcon_list = listplayers_via_rcon(
                    host, rcon_port, rcon_password, timeout=max(timeout, 5.0)
                )
                online = True
                source = "rcon"
            except Exception as exc:  # noqa: BLE001
                rcon_error = str(exc)
                logger.info("RCON listplayers failed for %s:%s: %s", host, rcon_port, exc)

        if source == "rcon":
            players = len(rcon_list)
            player_list = rcon_list
        else:
            list_count = len(a2s_list)
            players = max(a2s_players, list_count)
            player_list = a2s_list
            source = "a2s" if online else "offline"

        return {
            "online": online,
            "players": players,
            "max_players": a2s_max,
            "bots": a2s_bots,
            "player_list": player_list,
            "source": source,
            "a2s_players": a2s_players,
            "a2s_error": a2s_error,
            "rcon_error": rcon_error,
            "sampled_at": time.time(),
        }

    def player_count_hint(self, *, has_rcon_password: bool, snap: dict[str, Any]) -> str | None:
        online = bool(snap.get("online"))
        players = int(snap.get("players") or 0)
        if not has_rcon_password and online and players == 0:
            return (
                "Player count may be wrong: set RCON password under Servers "
                "(Sandstorm A2S often reports 0 humans)."
            )
        if has_rcon_password and snap.get("rcon_error") and online:
            return f"RCON listplayers failed: {snap.get('rcon_error')}"
        return None


sandstorm_adapter = SandstormAdapter()
