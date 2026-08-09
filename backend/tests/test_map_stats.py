"""Map popularity aggregation (unbiased ranking rules)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import PlayerCountSample
from app.services.map_stats import aggregate_map_stats


def _ts(minutes: int) -> datetime:
    base = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(minutes=minutes)


def _sample(
    *,
    server_id: int = 1,
    minutes: int,
    players: int,
    map_name: str | None,
    gamemode: str | None = "Checkpoint",
    online: bool = True,
) -> PlayerCountSample:
    return PlayerCountSample(
        server_id=server_id,
        recorded_at=_ts(minutes),
        players=players,
        max_players=32,
        online=online,
        roster_json="[]",
        map_name=map_name,
        gamemode=gamemode,
        lighting="Day",
    )


def test_empty_samples_do_not_count():
    samples = [
        _sample(minutes=0, players=0, map_name="Farmhouse"),
        _sample(minutes=1, players=0, map_name="Farmhouse"),
        _sample(minutes=2, players=8, map_name="Crossing"),
        _sample(minutes=3, players=8, map_name="Crossing"),
    ]
    result = aggregate_map_stats(
        samples,
        server_id=1,
        range_key="24h",
        from_time=_ts(0),
        to_time=_ts(10),
        min_active_minutes=0,
        max_delta_seconds=120,
    )
    maps = {r.map_name: r for r in result.rows}
    assert "Farmhouse" not in maps
    assert "Crossing" in maps
    assert maps["Crossing"].peak_players == 8
    assert maps["Crossing"].avg_players == 8.0


def test_untagged_samples_ignored():
    samples = [
        _sample(minutes=0, players=20, map_name=None),
        _sample(minutes=1, players=20, map_name=""),
        _sample(minutes=2, players=4, map_name="Refinery"),
        _sample(minutes=3, players=4, map_name="Refinery"),
    ]
    result = aggregate_map_stats(
        samples,
        server_id=1,
        range_key="24h",
        from_time=_ts(0),
        to_time=_ts(10),
        min_active_minutes=0,
        max_delta_seconds=120,
        combine_gamemodes=True,
    )
    assert len(result.rows) == 1
    assert result.rows[0].map_name == "Refinery"
    assert result.data_since == _ts(2)


def test_cycle_frequency_does_not_dominate_avg_rank():
    """Map A appears twice as often empty-filled; B has higher occupancy.

    Clock time would favor A; avg concurrent should favor B.
    """
    samples: list[PlayerCountSample] = []
    # A: many slots, only 2 players when occupied
    for m in range(0, 20):
        samples.append(
            _sample(minutes=m, players=2 if m % 2 == 0 else 0, map_name="MapA")
        )
    # B: fewer active slots, 10 players
    for m in range(20, 28):
        samples.append(
            _sample(minutes=m, players=10 if m % 2 == 0 else 0, map_name="MapB")
        )

    result = aggregate_map_stats(
        samples,
        server_id=1,
        range_key="24h",
        from_time=_ts(0),
        to_time=_ts(30),
        min_active_minutes=0,
        max_delta_seconds=120,
        combine_gamemodes=True,
    )
    assert result.rows[0].map_name == "MapB"
    assert result.rows[0].avg_players > result.rows[1].avg_players


def test_gamemode_split_and_combine():
    samples = [
        _sample(minutes=0, players=5, map_name="Farmhouse", gamemode="Checkpoint"),
        _sample(minutes=1, players=5, map_name="Farmhouse", gamemode="Checkpoint"),
        _sample(minutes=2, players=10, map_name="Farmhouse", gamemode="Push"),
        _sample(minutes=3, players=10, map_name="Farmhouse", gamemode="Push"),
    ]
    split = aggregate_map_stats(
        samples,
        server_id=1,
        range_key="24h",
        from_time=_ts(0),
        to_time=_ts(10),
        min_active_minutes=0,
        combine_gamemodes=False,
        max_delta_seconds=120,
    )
    assert len(split.rows) == 2
    modes = {r.gamemode for r in split.rows}
    assert modes == {"Checkpoint", "Push"}

    combined = aggregate_map_stats(
        samples,
        server_id=1,
        range_key="24h",
        from_time=_ts(0),
        to_time=_ts(10),
        min_active_minutes=0,
        combine_gamemodes=True,
        max_delta_seconds=120,
    )
    assert len(combined.rows) == 1
    assert combined.rows[0].map_name == "Farmhouse"
    assert combined.rows[0].gamemode == ""


def test_min_active_floor_marks_unqualified():
    samples = [
        _sample(minutes=0, players=32, map_name="OneShot"),
        _sample(minutes=1, players=32, map_name="OneShot"),
        # Steady map with lower avg but more exposure
        *[_sample(minutes=m, players=6, map_name="Steady") for m in range(2, 40)],
    ]
    result = aggregate_map_stats(
        samples,
        server_id=1,
        range_key="24h",
        from_time=_ts(0),
        to_time=_ts(50),
        min_active_minutes=30,
        max_delta_seconds=60,
        combine_gamemodes=True,
    )
    by_name = {r.map_name: r for r in result.rows}
    assert by_name["OneShot"].qualified is False
    assert by_name["Steady"].qualified is True
    # Qualified sorts first even if avg is lower
    assert result.rows[0].map_name == "Steady"


def test_player_minutes_uses_capped_delta():
    samples = [
        _sample(minutes=0, players=10, map_name="Gap"),
        # 10 minutes later - should cap at max_delta_seconds
        _sample(minutes=10, players=10, map_name="Gap"),
    ]
    result = aggregate_map_stats(
        samples,
        server_id=1,
        range_key="24h",
        from_time=_ts(0),
        to_time=_ts(20),
        min_active_minutes=0,
        max_delta_seconds=120,  # 2 minutes
        combine_gamemodes=True,
    )
    row = result.rows[0]
    # First sample: 10 players * 120s = 1200 player-seconds = 20 player-minutes
    # Second (last): same capped interval again
    assert row.player_minutes == pytest.approx(40.0, abs=0.01)


def test_alias_lookup_applied():
    samples = [
        _sample(minutes=0, players=3, map_name="Canyon"),
        _sample(minutes=1, players=3, map_name="Canyon"),
    ]
    result = aggregate_map_stats(
        samples,
        server_id=1,
        range_key="24h",
        from_time=_ts(0),
        to_time=_ts(5),
        min_active_minutes=0,
        max_delta_seconds=60,
        combine_gamemodes=True,
        alias_by_map={"Canyon": "Crossing"},
    )
    assert result.rows[0].alias == "Crossing"
