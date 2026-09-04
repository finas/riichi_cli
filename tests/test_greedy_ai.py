"""Tests for greedy_ai.py"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mahjong.core.tile import ALL_TILES_136, make_tiles_from_string
from mahjong.core.hand import Hand
from mahjong.core.player_state import Wind
from mahjong.engine.action import ActionType, AvailableActions
from mahjong.player.base import GameView, OpponentView
from mahjong.player.greedy_ai import GreedyAI, _cached_ukeire


def make_game_view(hand, seat=0, wind=Wind.EAST, score=25000,
                   opponents=None) -> GameView:
    return GameView(
        my_hand=hand,
        my_seat=seat,
        my_wind=wind,
        my_score=score,
        is_dealer=(wind == Wind.EAST),
        opponents=opponents or [],
        round_wind=Wind.EAST,
        remaining_tiles=70,
    )


class TestGreedyAIDiscard:
    def test_sanma_ukeire_excludes_removed_middle_manzu(self):
        """Sanma efficiency must not count impossible 2-8 manzu draws."""
        counts = [0] * 34
        for tile in make_tiles_from_string("13m123p456p789s東南"):
            counts[tile.index34] += 1
        key = tuple(counts)
        current_s = 1

        yonma = _cached_ukeire(key, key, current_s, False)
        sanma = _cached_ukeire(key, key, current_s, True)

        assert sanma < yonma

    def test_sanma_keeps_north_for_kita_value(self):
        """North should not be the default discard in Sanma efficiency ties."""
        ai = GreedyAI("test")
        hand = Hand()
        for t in make_tiles_from_string("123p456p789s東南西北"):
            hand.closed_tiles.append(t)
        opponents = [
            OpponentView(seat=1, name="s", score=35000,
                         seat_wind=Wind.SOUTH, is_dealer=False,
                         is_riichi=False),
            OpponentView(seat=2, name="w", score=35000,
                         seat_wind=Wind.WEST, is_dealer=False,
                         is_riichi=False),
        ]
        gv = make_game_view(hand, opponents=opponents)

        ranked = ai.rank_discards(gv, list(hand.closed_tiles))

        assert ranked[0].index34 != 30

    def test_discard_reduces_shanten(self):
        """AI should choose discard that minimizes shanten."""
        ai = GreedyAI("test")
        hand = Hand()
        tiles = make_tiles_from_string("123m456p789s東東")
        for t in tiles:
            hand.closed_tiles.append(t)
        # Add an isolated honor tile
        north = make_tiles_from_string("北")[0]
        hand.draw(north)

        gv = make_game_view(hand)
        discard = ai.choose_discard(gv, hand.closed_tiles)
        # Should discard the isolated 北
        assert discard.index34 == 30  # 北

    def test_always_tsumo(self):
        """AI should always tsumo when available."""
        ai = GreedyAI("test")
        hand = Hand()
        tiles = make_tiles_from_string("123m456p789s東東東南")
        for t in tiles:
            hand.closed_tiles.append(t)

        gv = make_game_view(hand)
        available = AvailableActions(player=0)
        available.can_tsumo = True
        available.can_discard = list(hand.closed_tiles)

        action = ai.choose_action(gv, available)
        assert action.action_type == ActionType.TSUMO

    def test_always_ron(self):
        """AI should always ron when available."""
        ai = GreedyAI("test")
        hand = Hand()

        gv = make_game_view(hand)
        available = AvailableActions(player=0)
        available.can_ron = True

        action = ai.choose_action(gv, available)
        assert action.action_type == ActionType.RON


class TestGreedyAIDefense:
    def test_defense_mode(self):
        """AI should enter defense when opponent is riichi and own shanten >= 2."""
        ai = GreedyAI("test")
        hand = Hand()
        # Bad hand with high shanten
        tiles = make_tiles_from_string("159m159p159s東南西北")
        for t in tiles:
            hand.closed_tiles.append(t)

        opp = OpponentView(
            seat=1, name="opp", score=25000, seat_wind=Wind.SOUTH,
            is_dealer=False, is_riichi=True, melds=[], discard_pool=[],
            discard_called=[], discard_is_tsumogiri=[], num_closed_tiles=13,
        )

        gv = make_game_view(hand, opponents=[opp])
        assert ai._should_defend(gv)

    def test_high_value_dealer_can_push_at_two_shanten(self):
        ai = GreedyAI("test")
        hand = Hand()
        for t in make_tiles_from_string("555m123p789s東南西白發"):
            hand.closed_tiles.append(t)
        opp = OpponentView(
            seat=1, name="opp", score=25000, seat_wind=Wind.SOUTH,
            is_dealer=False, is_riichi=True, melds=[], discard_pool=[],
            discard_called=[], discard_is_tsumogiri=[], num_closed_tiles=13,
        )
        gv = make_game_view(hand, opponents=[opp], wind=Wind.EAST)
        gv.dora_indicators = make_tiles_from_string("4m")
        gv.remaining_tiles = 50

        assert ai._should_push_against_riichi(gv, current_shanten=2)

    def test_weak_two_shanten_still_folds(self):
        ai = GreedyAI("test")
        hand = Hand()
        for t in make_tiles_from_string("146m146p146s東南西白發"):
            hand.closed_tiles.append(t)
        opp = OpponentView(
            seat=1, name="opp", score=25000, seat_wind=Wind.SOUTH,
            is_dealer=False, is_riichi=True, melds=[], discard_pool=[],
            discard_called=[], discard_is_tsumogiri=[], num_closed_tiles=13,
        )
        gv = make_game_view(hand, opponents=[opp])

        assert not ai._should_push_against_riichi(gv, current_shanten=2)


class TestGreedyAIStrategy:
    def test_distant_open_call_is_rejected(self):
        ai = GreedyAI("test")
        hand = Hand()
        for t in make_tiles_from_string("159m159p159s東南西北"):
            hand.closed_tiles.append(t)
        gv = make_game_view(hand)
        # The conservative gate is directly testable without constructing a
        # full engine meld: an unrelated open call has no yaku route here.
        assert not ai._call_has_value(gv, 0)

    def test_tenpai_value_uses_scoring_engine(self):
        """A legal riichi tenpai shape receives a non-zero value estimate."""
        ai = GreedyAI("test")
        hand = Hand()
        for t in make_tiles_from_string("123m456m789m123p東9s"):
            hand.closed_tiles.append(t)

        gv = make_game_view(hand)
        discard = make_tiles_from_string("9s")[0]
        tenpai = hand.to_34_array()
        tenpai[discard.index34] -= 1
        visible = list(tenpai)

        value = ai._estimate_tenpai_value(gv, hand, discard, tenpai, [27], visible)

        assert value >= 1000

    def test_kokushi_route_discards_simple_tile(self):
        """Keep all terminal/honor types when kokushi is the best route."""
        ai = GreedyAI("test")
        hand = Hand()
        for t in make_tiles_from_string("19m19p19s東南西北白發中2m"):
            hand.closed_tiles.append(t)

        gv = make_game_view(hand)
        discard = ai.choose_discard(gv, hand.closed_tiles)

        assert discard.index34 == 1  # 2m is dead weight for kokushi

    def test_common_genbutsu_beats_tile_safe_to_one_riichi_only(self):
        """A tile discarded by every riichi opponent must rank safest."""
        ai = GreedyAI("test")
        hand = Hand()
        for t in make_tiles_from_string("123m456p789s東南西白發"):
            hand.closed_tiles.append(t)

        opp1 = OpponentView(
            seat=1, name="opp1", score=25000, seat_wind=Wind.SOUTH,
            is_dealer=False, is_riichi=True, melds=[],
            discard_pool=make_tiles_from_string("1m"),
            discard_called=[False], discard_is_tsumogiri=[False],
            num_closed_tiles=13,
        )
        opp2 = OpponentView(
            seat=2, name="opp2", score=25000, seat_wind=Wind.WEST,
            is_dealer=False, is_riichi=True, melds=[],
            discard_pool=make_tiles_from_string("1m"),
            discard_called=[False], discard_is_tsumogiri=[False],
            num_closed_tiles=13,
        )

        gv = make_game_view(hand, opponents=[opp1, opp2])
        ranked = ai.rank_discards(gv, hand.closed_tiles)

        assert ranked[0].index34 == 0  # 1m is common genbutsu

    def test_honitsu_route_sheds_off_suit_tiles(self):
        """When hand has strong single suit + honors, off-suit tiles are discarded first."""
        ai = GreedyAI("test")
        hand = Hand()
        # 8 sou tiles + 3 white dragons + isolated 2m + 8p
        for t in make_tiles_from_string("123s456s78s白白白2m8p"):
            hand.closed_tiles.append(t)

        gv = make_game_view(hand)
        discard = ai.choose_discard(gv, hand.closed_tiles)
        # Discard must be 2m (index34=1) or 8p (index34=16), not breaking sou or dragons
        assert discard.index34 in (1, 16)

    def test_tanyao_route_sheds_isolated_terminals(self):
        """When hand is close to all-simples, isolated 1/9/honors are shed first."""
        ai = GreedyAI("test")
        hand = Hand()
        # 10 simple tiles + isolated 1m + isolated 9p
        for t in make_tiles_from_string("234m345p456s67s1m9p"):
            hand.closed_tiles.append(t)

        gv = make_game_view(hand)
        discard = ai.choose_discard(gv, hand.closed_tiles)
        # Discard must be 1m (index 0) or 9p (index 17)
        assert discard.index34 in (0, 17)
