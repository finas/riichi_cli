"""Full-game invariant tests using AI self-play.

These act as a safety net for engine refactoring: they run complete
games with GreedyAI players and assert global invariants hold.
"""

import random
from collections import Counter

import pytest

from mahjong.engine.event import EventBus
from mahjong.engine.game import GameConfig, GameState
from mahjong.engine.round import run_round
from mahjong.player.base import build_game_view
from mahjong.player.greedy_ai import GreedyAI


def _assert_tile_conservation(round_state):
    """Every physical tile belongs to exactly one visible/unseen location."""
    locations = []
    labels = []
    wall = round_state.wall

    locations.extend(wall.live_wall)
    labels.extend(["live_wall"] * len(wall.live_wall))
    drawn_rinshan_ids = {tile.id for tile in wall.rinshan_tiles_drawn}
    remaining_dead = [tile for tile in wall.dead_wall
                      if tile.id not in drawn_rinshan_ids]
    locations.extend(remaining_dead)
    labels.extend(["dead_wall"] * len(remaining_dead))
    locations.extend(wall.kan_reserved_tiles)
    labels.extend(["kan_reserved"] * len(wall.kan_reserved_tiles))

    for player in round_state.players:
        locations.extend(player.hand.closed_tiles)
        labels.extend([f"{player.name}:closed"] * len(player.hand.closed_tiles))
        locations.extend(player.kita_tiles)
        labels.extend([f"{player.name}:kita"] * len(player.kita_tiles))
        for tile, called in zip(
                player.hand.discard_pool, player.hand.discard_called):
            if not called:
                locations.append(tile)
                labels.append(f"{player.name}:discard")
        for meld in player.hand.melds:
            locations.extend(meld.tiles)
            labels.extend([f"{player.name}:meld:{meld.meld_type.value}"] * len(meld.tiles))

    tile_ids = [tile.id for tile in locations]
    counts = Counter(tile_ids)
    duplicates = {tile_id: [label for current, label in zip(tile_ids, labels)
                            if current == tile_id]
                  for tile_id, count in counts.items() if count > 1}
    expected = {tile.id for tile in wall.all_tiles}
    assert len(tile_ids) == wall.total_tiles, duplicates
    assert len(set(tile_ids)) == wall.total_tiles, duplicates
    assert set(tile_ids) == expected, (duplicates, expected - set(tile_ids))


def _play_full_game(seed: int, config: GameConfig) -> GameState:
    """Run a complete AI-vs-AI game and return the finished GameState.

    Asserts the point-conservation invariant after every round:
    player scores plus riichi sticks on the table must always equal
    the total starting points.
    """
    random.seed(seed)
    names = [f"AI{i}" for i in range(config.num_players)]
    game = GameState(config, names, EventBus())
    ais = [GreedyAI(n) for n in names]
    total_points = config.num_players * config.starting_score

    rounds_played = 0
    while not game.is_finished:
        round_state = game.setup_round()

        def get_action(player_idx, available):
            gv = build_game_view(
                player_idx, game.players,
                game.round_wind, game.honba, game.riichi_sticks,
                round_state.wall.remaining,
                round_state.wall.dora_indicators,
            )
            return ais[player_idx].choose_action(gv, available)

        result = run_round(round_state, get_action)
        assert result is not None, "round ended without a result"
        _assert_tile_conservation(round_state)
        game.advance_round(result)

        current = sum(p.score for p in game.players) + game.riichi_sticks * 1000
        assert current == total_points, (
            f"point conservation violated after round {rounds_played}: "
            f"{current} != {total_points}"
        )

        rounds_played += 1
        assert rounds_played < 100, "game did not terminate"

    # At game end all leftover sticks must have been distributed
    assert game.riichi_sticks == 0
    assert sum(p.score for p in game.players) == total_points
    return game


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_yonma_hanchan_completes(seed):
    """4-player hanchan: AI self-play must finish without errors."""
    config = GameConfig(num_players=4)
    game = _play_full_game(seed, config)
    assert game.is_finished
    assert len(game.round_results) > 0


@pytest.mark.parametrize("seed", [3, 11])
def test_sanma_hanchan_completes(seed):
    """3-player hanchan: AI self-play must finish without errors."""
    config = GameConfig(num_players=3, is_sanma=True)
    game = _play_full_game(seed, config)
    assert game.is_finished
    assert len(game.round_results) > 0


def test_tonpuu_completes():
    """East-only game must finish without errors."""
    config = GameConfig(num_players=4, is_tonpuu=True)
    game = _play_full_game(5, config)
    assert game.is_finished
