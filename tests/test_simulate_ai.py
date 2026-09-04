"""Test match simulation engine (Phase 5)."""

from scripts.simulate_ai import simulate_matches, run_single_simulation


def test_run_single_simulation():
    scores, results, placements = run_single_simulation(num_players=4, is_tonpuu=True)
    assert len(scores) == 4
    assert len(results) >= 1  # 1 round if someone busts (tobi), otherwise full tonpuu
    # Total scores in standard mahjong sum to 100,000 (starting 25,000 x 4) minus riichi sticks on table
    assert sum(scores) >= 90000


def test_simulate_matches():
    stats = simulate_matches(num_games=2, num_players=4, is_tonpuu=True, seed=123)
    assert stats.total_games == 2
    assert stats.total_rounds >= 8
    # Ensure every game produced valid 1st/2nd/3rd/4th placements
    for i in range(4):
        assert sum(stats.placements[i]) == 2
