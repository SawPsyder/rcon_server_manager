"""Public Palworld map shares must not leak platform account ids."""

from fastapi import HTTPException

from app.api.map_share import _unavailable, redact_public_world
from app.schemas import PalworldWorldOut, PalworldWorldPlayer


def test_redact_public_world_replaces_user_ids_with_opaque_ids():
    world = PalworldWorldOut(
        enabled=True,
        players=[
            PalworldWorldPlayer(name="Jay", user_id="steam_76561198012345678"),
            PalworldWorldPlayer(name="Kit", user_id="gdk_2535470764765514"),
            PalworldWorldPlayer(name="NoId", user_id=""),
        ],
    )
    out = redact_public_world(world, server_id=7)
    jay, kit, no_id = out.players
    assert jay.user_id.startswith("p_")
    assert kit.user_id.startswith("p_")
    assert jay.user_id != kit.user_id
    assert "76561198012345678" not in jay.user_id
    assert "2535470764765514" not in kit.user_id
    assert no_id.user_id == ""
    assert [p.name for p in out.players] == ["Jay", "Kit", "NoId"]

    again = redact_public_world(world, server_id=7)
    assert [p.user_id for p in again.players] == [p.user_id for p in out.players]
    other_server = redact_public_world(world, server_id=8)
    assert other_server.players[0].user_id != jay.user_id

    # Original payload is untouched so the admin /world route can still show ids.
    assert world.players[0].user_id.startswith("steam_")


def test_public_map_errors_do_not_leak_upstream_status():
    wrapped = _unavailable(3, HTTPException(status_code=400, detail="Server has no admin password configured"))
    assert wrapped.status_code == 502
    assert wrapped.detail == "Map source unavailable"
