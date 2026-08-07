#!/usr/bin/env python3
"""A fake Palworld dedicated server that speaks the real REST API.

Owning a Palworld server just to check the integration is a poor trade, so this
stands in for one. It implements all 12 documented endpoints with HTTP Basic
auth and data that actually moves - server FPS jitters, players join and leave,
uptime and in-game days climb - which is what the charts, presence tracking and
roster tooltips need in order to show anything interesting.

    python scripts/fake_palworld_server.py --port 8212 --password test123

Then add a server in the UI: type Palworld, host 127.0.0.1, API port 8212,
admin password test123.

Failure modes worth exercising are behind flags:

    --reject-auth        every request answers 401 (wrong AdminPassword)
    --gamedata-disabled  /game-data refuses, as it does without the launch flag
    --slow-save          /save blocks for 8s, like a large world

Stdlib only; nothing here is imported by the app.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

API_PREFIX = "/v1/api"
SERVER_VERSION = "v1.0.2"

# Steam IDs are 17 digits; the API prefixes them with the platform
FAKE_PLAYERS = [
    ("Lyra", "lyra_builds", "76561198084350159"),
    ("Tomo", "tomonokai", "76561198012345678"),
    ("Kestrel", "kestrelplays", "76561197960287930"),
    ("Anvil", "anvil_smith", "76561198234567890"),
    # One non-Steam player, to prove the platform prefix survives untouched
    ("XboxPal", "xpal", "xsx_2535412345678901"),
]

# Stable world positions (UE/save coords) so the admin map doesn't jitter every
# poll. Converted from well-known in-game map coords via the wiki transform
# (mapX, mapY) → sav: x = mapY*459 - 123888, y = mapX*459 + 158000.
# Approx. map spots: plateau / forest / desert / volcano / starting isle.
FAKE_PLAYER_WORLD_XY: dict[str, tuple[float, float]] = {
    "76561198084350159": (-364404.0, 158000.0),   # map ~ (0, -524)
    "76561198012345678": (-180000.0, 50000.0),
    "76561197960287930": (-50000.0, 250000.0),
    "76561198234567890": (-420000.0, -80000.0),
    "xsx_2535412345678901": (-280000.0, 100000.0),
}

# Base camps sit near their guild owner with a small fixed offset
FAKE_CAMP_OFFSET = (3500.0, -2200.0)


class World:
    """Mutable server state, ticked on read so every poll sees fresh numbers."""

    def __init__(self, *, seed: int = 7, max_players: int = 32) -> None:
        self.rng = random.Random(seed)
        self.started = time.time()
        self.max_players = max_players
        self.lock = threading.RLock()
        self.banned: set[str] = set()
        self.announcements: list[str] = []
        self.saved_at: float | None = None
        # Everyone but the last player starts online
        self.online = {p[2] for p in FAKE_PLAYERS[:3]}
        self.levels = {p[2]: self.rng.randint(8, 44) for p in FAKE_PLAYERS}
        self.buildings = {p[2]: self.rng.randint(20, 260) for p in FAKE_PLAYERS}
        self.instances = {p[2]: f"{i:032X}" for i, p in enumerate(FAKE_PLAYERS, start=1)}

    # --- derived values ----------------------------------------------------

    @property
    def uptime(self) -> int:
        return int(time.time() - self.started)

    @property
    def fps(self) -> int:
        """Drifts around 58 with a slow wave, so the chart isn't a flat line."""
        wave = math.sin(self.uptime / 45.0) * 4
        return max(1, int(58 + wave + self.rng.uniform(-1.5, 1.5)))

    def tick(self) -> None:
        """Occasionally move a player in or out, so presence has something to do."""
        with self.lock:
            if self.rng.random() < 0.12:
                candidate = self.rng.choice(FAKE_PLAYERS)[2]
                if candidate in self.banned:
                    return
                if candidate in self.online:
                    self.online.discard(candidate)
                else:
                    self.online.add(candidate)

    def user_id(self, steam_id: str) -> str:
        return steam_id if "_" in steam_id else f"steam_{steam_id}"

    # --- payloads ----------------------------------------------------------

    def info(self) -> dict[str, Any]:
        return {
            "version": SERVER_VERSION,
            "servername": "Fake Palworld Server",
            "description": "Stand-in server for SandstormServerManager development.",
            "worldguid": "A7E97BAA767DB9029EF013BB71E993A0",
        }

    def metrics(self) -> dict[str, Any]:
        fps = self.fps
        return {
            "serverfps": fps,
            "currentplayernum": len(self.online),
            "serverframetime": round(1000.0 / fps, 4),
            "maxplayernum": self.max_players,
            "uptime": self.uptime,
            "basecampnum": 3 + len(self.online),
            "days": 1 + self.uptime // 600,
        }

    def players(self) -> dict[str, Any]:
        rows = []
        for name, account, steam_id in FAKE_PLAYERS:
            if steam_id not in self.online:
                continue
            rows.append(
                {
                    "name": name,
                    "accountName": account,
                    "playerId": self.instances[steam_id],
                    "userId": self.user_id(steam_id),
                    "ip": f"10.0.0.{FAKE_PLAYERS.index((name, account, steam_id)) + 20}",
                    # Documented as a double, not an int
                    "ping": round(self.rng.uniform(8.0, 92.0), 2),
                    "location_x": FAKE_PLAYER_WORLD_XY[steam_id][0],
                    "location_y": FAKE_PLAYER_WORLD_XY[steam_id][1],
                    "level": self.levels[steam_id],
                    "building_count": self.buildings[steam_id],
                }
            )
        return {"players": rows}

    def settings(self) -> dict[str, Any]:
        # A representative slice; note the game's real misspellings are preserved
        return {
            "Difficulty": "None",
            "DayTimeSpeedRate": 1.0,
            "NightTimeSpeedRate": 1.0,
            "ExpRate": 1.5,
            "PalCaptureRate": 1.0,
            "PalDamageRateAttack": 1.0,
            "PlayerDamageRateAttack": 1.0,
            "PlayerStomachDecreaceRate": 1.0,
            "PlayerStaminaDecreaceRate": 1.0,
            "PlayerAutoHPRegeneRate": 1.0,
            "PlayerAutoHpRegeneRateInSleep": 1.0,
            "PalStaminaDecreaceRate": 1.0,
            "DeathPenalty": "All",
            "bEnablePlayerToPlayerDamage": False,
            "bEnableFriendlyFire": False,
            "bEnableInvaderEnemy": True,
            "bIsPvP": False,
            "bIsMultiplay": True,
            "DropItemMaxNum": 3000,
            "BaseCampMaxNum": 128,
            "BaseCampWorkerMaxNum": 15,
            "GuildPlayerMaxNum": 20,
            "WorkSpeedRate": 1.0,
            "CoopPlayerMaxNum": 4,
            "ServerPlayerMaxNum": self.max_players,
            "ServerName": "Fake Palworld Server",
            "ServerDescription": "Stand-in server for development.",
            "PublicPort": 8211,
            "PublicIP": "",
            "RCONEnabled": False,
            "RCONPort": 25575,
            "Region": "",
            "bUseAuth": True,
            "BanListURL": "",
            "RESTAPIEnabled": True,
            "RESTAPIPort": 8212,
            "bShowPlayerList": True,
            "AllowConnectPlatform": "Steam",
            "bIsUseBackupSaveData": True,
            "LogFormatType": "Text",
        }

    def game_data(self) -> dict[str, Any]:
        actors: list[dict[str, Any]] = []
        for name, _account, steam_id in FAKE_PLAYERS:
            if steam_id not in self.online:
                continue
            instance = self.instances[steam_id]
            guild = f"{name}'s Guild"
            wx, wy = FAKE_PLAYER_WORLD_XY[steam_id]
            actors.append(
                {
                    "Type": "Character",
                    "InstanceID": instance,
                    "UnitType": "Player",
                    "NickName": name,
                    # game-data spells it lowercase, unlike /players' userId
                    "userid": self.user_id(steam_id),
                    "ip": "10.0.0.20",
                    "level": self.levels[steam_id],
                    "HP": self.rng.randint(200, 900),
                    "MaxHP": 900,
                    "GuildID": instance[:8],
                    "GuildName": guild,
                    "Class": "PlayerCharacter",
                    "Action": "Idle",
                    "LocationX": wx,
                    "LocationY": wy,
                    "LocationZ": 1200.0,
                    # Really a string in the API, not a boolean
                    "IsActive": "true",
                }
            )
            for pal in range(self.rng.randint(2, 5)):
                actors.append(
                    {
                        "Type": "Character",
                        "InstanceID": f"{instance[:24]}{pal:08X}",
                        "UnitType": "OtomoPal" if pal == 0 else "BaseCampPal",
                        "NickName": f"Pal{pal}",
                        "TrainerInstanceID": instance,
                        "TrainerNickName": name,
                        "level": self.rng.randint(1, 40),
                        "HP": 400,
                        "MaxHP": 400,
                        "GuildName": guild,
                        "LocationX": 0.0,
                        "LocationY": 0.0,
                        "LocationZ": 0.0,
                        "IsActive": "true",
                    }
                )
            actors.append(
                {
                    "Type": "PalBox",
                    "GuildID": instance[:8],
                    "GuildName": guild,
                    "Class": "PalBox",
                    "LocationX": wx + FAKE_CAMP_OFFSET[0],
                    "LocationY": wy + FAKE_CAMP_OFFSET[1],
                    "LocationZ": 120.0,
                }
            )
        for wild in range(6):
            actors.append(
                {
                    "Type": "Character",
                    "InstanceID": f"WILD{wild:028X}",
                    "UnitType": "WildPal",
                    "level": self.rng.randint(1, 50),
                    "LocationX": 0.0,
                    "LocationY": 0.0,
                    "LocationZ": 0.0,
                    "IsActive": "true",
                }
            )
        return {
            # Server local time, deliberately not ISO 8601 - as the real API does
            "Time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "FPS": float(self.fps),
            "AverageFPS": round(float(self.fps) - 1.4, 2),
            "ActorData": actors,
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "PalServer/fake"
    world: World
    password: str
    reject_auth: bool
    gamedata_disabled: bool
    slow_save: bool
    verbose: bool

    # --- plumbing ----------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.verbose:
            super().log_message(fmt, *args)

    def _send(self, status: int, payload: Any = None) -> None:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _text(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if self.reject_auth:
            return False
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
        except Exception:  # noqa: BLE001
            return False
        user, _, password = decoded.partition(":")
        # The real API only accepts the account name "admin"
        return user == "admin" and password == self.password

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return {}

    def _route(self) -> str | None:
        path = self.path.split("?", 1)[0].rstrip("/")
        if not path.startswith(API_PREFIX):
            return None
        return path[len(API_PREFIX) :] or "/"

    # --- verbs -------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        route = self._route()
        if route is None:
            self._text(404, "Not Found")
            return
        if not self._authorized():
            self._text(401, "Unauthorized.")
            return

        self.world.tick()
        if route == "/info":
            self._send(200, self.world.info())
        elif route == "/metrics":
            self._send(200, self.world.metrics())
        elif route == "/players":
            self._send(200, self.world.players())
        elif route == "/settings":
            self._send(200, self.world.settings())
        elif route == "/game-data":
            if self.gamedata_disabled:
                # What the real server answers without -enable-gamedata-api
                self._text(200, "GameData API is not enabled")
            else:
                self._send(200, self.world.game_data())
        else:
            self._text(404, "Not Found")

    def do_POST(self) -> None:  # noqa: N802
        route = self._route()
        if route is None:
            self._text(404, "Not Found")
            return
        if not self._authorized():
            self._text(401, "Unauthorized.")
            return

        body = self._body()
        world = self.world

        if route == "/announce":
            message = str(body.get("message") or "")
            if not message:
                self._text(400, "Bad request.")
                return
            with world.lock:
                world.announcements.append(message)
            print(f"[announce] {message}", flush=True)
            self._text(200, "The message was announced.")

        elif route in ("/kick", "/ban", "/unban"):
            userid = str(body.get("userid") or "")
            if not userid:
                self._text(400, "Bad request.")
                return
            steam_id = userid.split("_", 1)[-1] if userid.startswith("steam_") else userid
            with world.lock:
                if route == "/unban":
                    world.banned.discard(steam_id)
                else:
                    world.online.discard(steam_id)
                    if route == "/ban":
                        world.banned.add(steam_id)
            verb = {"/kick": "kicked", "/ban": "banned", "/unban": "unbanned"}[route]
            print(f"[{verb}] {userid}", flush=True)
            self._text(200, f"The player was {verb}.")

        elif route == "/save":
            if self.slow_save:
                time.sleep(8)
            world.saved_at = time.time()
            print("[save] world saved", flush=True)
            self._text(200, "Successfully saved the world.")

        elif route == "/shutdown":
            if "waittime" not in body:
                self._text(400, "Bad request.")
                return
            print(
                f"[shutdown] in {body.get('waittime')}s: {body.get('message') or ''}",
                flush=True,
            )
            self._text(200, "The server will shutdown.")

        elif route == "/stop":
            print("[stop] force stopped (still serving, this is a fake)", flush=True)
            self._text(200, "The server force stopped.")

        else:
            self._text(404, "Not Found")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8212)
    parser.add_argument("--password", default="test123", help="the fake AdminPassword")
    parser.add_argument("--max-players", type=int, default=32)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--reject-auth", action="store_true", help="always answer 401")
    parser.add_argument(
        "--gamedata-disabled",
        action="store_true",
        help="refuse /game-data, as a server without -enable-gamedata-api does",
    )
    parser.add_argument("--slow-save", action="store_true", help="make /save block for 8s")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every request")
    args = parser.parse_args()

    Handler.world = World(seed=args.seed, max_players=args.max_players)
    Handler.password = args.password
    Handler.reject_auth = args.reject_auth
    Handler.gamedata_disabled = args.gamedata_disabled
    Handler.slow_save = args.slow_save
    Handler.verbose = args.verbose

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(
        f"Fake Palworld REST API on http://{args.host}:{args.port}{API_PREFIX} "
        f"(admin / {args.password})",
        flush=True,
    )
    if args.reject_auth:
        print("  !! --reject-auth: every request will answer 401", flush=True)
    if args.gamedata_disabled:
        print("  !! --gamedata-disabled: /game-data will refuse", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.", flush=True)
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
