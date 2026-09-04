"""Greedy AI player - optimizes for shanten reduction with basic defense."""

from functools import lru_cache
from typing import List, Optional, Tuple

from mahjong.core.tile import (
    ALL_TILES_136,
    Tile,
    YAOCHU_INDICES,
    next_tile_index,
    tiles_to_34_array,
)
from mahjong.core.meld import Meld
from mahjong.core.player_state import Wind
from mahjong.engine.action import Action, ActionType, AvailableActions
from mahjong.player.base import Player, GameView
from mahjong.player.ai_features import PublicFeatures, extract_public_features
from mahjong.rules.shanten import shanten, shanten_chiitoi, shanten_kokushi, shanten_standard


@lru_cache(maxsize=65536)
def _cached_ukeire(counts: tuple[int, ...], visible: tuple[int, ...],
                   current_shanten: int, is_sanma: bool = False) -> int:
    """Count effective tiles for a fixed post-discard hand shape."""
    total = 0
    for draw_idx in range(34):
        # 2-8 manzu are removed from the Sanma wall and can never be an
        # effective draw, even though the generic 34-tile shanten calculator
        # still knows about those tile indices.
        if is_sanma and 1 <= draw_idx <= 7:
            continue
        if counts[draw_idx] >= 4:
            continue
        probe = list(counts)
        probe[draw_idx] += 1
        if shanten(probe) < current_shanten:
            total += max(0, 4 - visible[draw_idx])
    return total


@lru_cache(maxsize=8192)
def _cached_continuation_ukeire(counts: tuple[int, ...], visible: tuple[int, ...],
                                current_shanten: int,
                                is_sanma: bool = False) -> int:
    """Bounded two-ply score for a post-discard 2-shanten shape.

    Only draws that immediately improve shanten are explored.  For each such
    draw, the best next discard's ukeire is retained.  This is substantially
    cheaper than searching every draw/discard branch and is used only for the
    strongest few candidates.
    """
    if current_shanten != 2:
        return 0

    improving_draws = []
    for draw_idx in range(34):
        if is_sanma and 1 <= draw_idx <= 7:
            continue
        remaining = max(0, 4 - visible[draw_idx])
        if not remaining or counts[draw_idx] >= 4:
            continue
        drawn = list(counts)
        drawn[draw_idx] += 1
        if shanten(drawn) >= current_shanten:
            continue

        improving_draws.append((remaining, draw_idx))

    # Two representative high-availability draws are enough to distinguish
    # continuation quality while keeping live turns fast.
    score = 0
    for remaining, draw_idx in sorted(improving_draws, reverse=True)[:2]:
        drawn = list(counts)
        drawn[draw_idx] += 1

        best_next = 0
        for discard_idx, amount in enumerate(drawn):
            if amount <= 0:
                continue
            after = list(drawn)
            after[discard_idx] -= 1
            next_s = shanten(after)
            if next_s == 1:
                best_next = max(
                    best_next,
                    _cached_ukeire(tuple(after), visible, next_s, is_sanma),
                )
        score += remaining * best_next
    return score


@lru_cache(maxsize=8192)
def _cached_refined_continuation_ukeire(
        counts: tuple[int, ...], visible: tuple[int, ...],
        current_shanten: int, is_sanma: bool = False) -> int:
    """Two-ply continuation score with branch-local visibility."""
    if current_shanten != 2:
        return 0

    improving_draws = []
    for draw_idx in range(34):
        if is_sanma and 1 <= draw_idx <= 7:
            continue
        remaining = max(0, 4 - visible[draw_idx])
        if not remaining or counts[draw_idx] >= 4:
            continue
        drawn = list(counts)
        drawn[draw_idx] += 1
        if shanten(drawn) < current_shanten:
            improving_draws.append((remaining, draw_idx))

    score = 0
    for remaining, draw_idx in sorted(improving_draws, reverse=True)[:2]:
        drawn = list(counts)
        drawn[draw_idx] += 1
        drawn_visible = list(visible)
        drawn_visible[draw_idx] = min(4, drawn_visible[draw_idx] + 1)

        best_next = 0
        for discard_idx, amount in enumerate(drawn):
            if amount <= 0:
                continue
            after = list(drawn)
            after[discard_idx] -= 1
            next_s = shanten(after)
            if next_s != 1:
                continue
            best_next = max(
                best_next,
                _cached_ukeire(
                    tuple(after), tuple(drawn_visible), next_s, is_sanma
                ),
            )
        score += remaining * best_next
    return score


class GreedyAI(Player):
    """AI that greedily minimizes shanten with basic defensive play.

    Strategy:
    - Offense: Choose discards that minimize shanten number
    - Calling: Accept pon/chi if it reduces shanten (but protect menzen potential)
    - Riichi: Declare if tenpai and score >= 1000
    - Defense: When opponent riichi and own shanten >= 2, play safe tiles
    """

    def _extract_features(self, game_view: GameView) -> PublicFeatures:
        """Extract public features; subclasses may normalize their view."""
        return extract_public_features(game_view)

    def _dora_indices_for_scoring(self, game_view: GameView) -> List[int]:
        """Return actual dora indices used by the score estimator."""
        is_sanma = len(game_view.opponents) == 2
        return [
            next_tile_index(tile.index34, is_sanma)
            for tile in game_view.dora_indicators
        ]

    def _visible_after_discard(self, visible: List[int],
                               discard_tile: Tile) -> tuple[int, ...]:
        """Return visibility for the post-discard hand shape."""
        return tuple(visible)

    def _wait_count_after_discard(self, visible: List[int],
                                  discard_tile: Tile,
                                  waits: List[int]) -> int:
        """Count waits using the baseline pre-discard visibility."""
        return sum(max(0, 4 - visible[wait]) for wait in waits)

    def _riichi_tie_prefers_tile(self, tile: Tile, best_tile: Tile,
                                 dora_indices: set[int]) -> bool:
        """Preserve the baseline riichi dora tie-break behavior."""
        return (
            not best_tile.is_red
            and tile.index34 in dora_indices
            and best_tile.index34 not in dora_indices
        )

    def _continuation_ukeire(self, counts: tuple[int, ...],
                             visible: tuple[int, ...],
                             current_shanten: int,
                             is_sanma: bool = False) -> int:
        """Return the baseline continuation score."""
        return _cached_continuation_ukeire(
            counts, visible, current_shanten, is_sanma
        )

    def choose_action(self, game_view: GameView,
                      available: AvailableActions) -> Action:
        """Choose the best available action."""
        # Always tsumo if possible
        if available.can_tsumo:
            return Action(ActionType.TSUMO, available.player)

        # Always ron if possible
        if available.can_ron:
            return Action(ActionType.RON, available.player)

        # Consider riichi
        if available.can_riichi:
            if self._should_declare_riichi(game_view, available.riichi_candidates):
                best_discard = self.choose_riichi_discard(
                    game_view, available.riichi_candidates)
                return Action(ActionType.RIICHI, available.player,
                              riichi_discard=best_discard)

        # Take kita only when North tile doesn't worsen shanten
        # (e.g. skip if North is forming a pair head or triplet)
        if available.can_kita:
            current_34 = game_view.my_hand.to_34_array()
            test_34 = list(current_34)
            test_34[30] -= 1  # North = index34 30
            if shanten(test_34) <= shanten(current_34):
                return Action(ActionType.KITA, available.player)

        # Consider ankan (if it doesn't worsen shanten)
        if available.can_ankan:
            for kan_tiles in available.can_ankan:
                return Action(ActionType.ANKAN, available.player,
                              tile=kan_tiles[0])

        # Consider shouminkan
        if available.can_shouminkan:
            # Only kan if not in danger
            if not self._should_defend(game_view):
                return Action(ActionType.SHOUMINKAN, available.player,
                              tile=available.can_shouminkan[0])

        # Consider pon
        if available.can_pon:
            if self._should_call_pon(game_view, available.can_pon[0]):
                return Action(ActionType.PON, available.player,
                              meld=available.can_pon[0])

        # Consider chi
        if available.can_chi:
            for chi_meld in available.can_chi:
                if self._should_call_chi(game_view, chi_meld):
                    return Action(ActionType.CHI, available.player,
                                  meld=chi_meld)

        # Default: discard
        if available.can_discard:
            tile = self.choose_discard(game_view, available.can_discard)
            return Action(ActionType.DISCARD, available.player, tile=tile)

        return Action(ActionType.SKIP, available.player)

    def choose_discard(self, game_view: GameView,
                       available_discards: List[Tile]) -> Tile:
        """Choose the best tile to discard."""
        hand = game_view.my_hand
        self._current_opponents = game_view.opponents
        self._current_dora_indicators = game_view.dora_indicators
        self._current_game_view = game_view

        try:
            # If defending, play safe
            if self._should_defend(game_view, available_discards):
                safe = self._find_safe_tile(game_view, available_discards)
                if safe:
                    return safe

            # Offense: minimize shanten
            return self._best_discard_for_shanten(
                hand, available_discards, game_view.dora_indicators,
                is_sanma=len(game_view.opponents) == 2,
                my_wind=game_view.my_wind,
                round_wind=game_view.round_wind)
        finally:
            self._current_opponents = []
            self._current_dora_indicators = []
            self._current_game_view = None
    def choose_riichi_discard(self, game_view: GameView,
                              riichi_candidates: List[Tile]) -> Tile:
        """Choose the best riichi discard (maximize waiting tiles)."""
        hand = game_view.my_hand
        features = self._extract_features(game_view)
        visible = features.visible_34
        dora_indices = features.dora_indices
        best_tile = riichi_candidates[0]
        best_waits = 0
        best_value = -1

        from mahjong.rules.agari import get_waiting_tiles

        for tile in riichi_candidates:
            test_34 = hand.to_34_array()
            test_34[tile.index34] -= 1
            waits = get_waiting_tiles(test_34)
            wait_count = self._wait_count_after_discard(
                visible, tile, waits
            )
            value_ev = self._estimate_tenpai_value(
                game_view, hand, tile, test_34, waits, visible
            )
            # Never throw a red five when an equivalent wait is available.
            if (wait_count > best_waits or
                    (wait_count == best_waits and value_ev > best_value) or
                    (wait_count == best_waits and value_ev == best_value
                     and best_tile.is_red and not tile.is_red) or
                    (wait_count == best_waits and value_ev == best_value
                     and self._riichi_tie_prefers_tile(
                         tile, best_tile, dora_indices))):
                best_waits = wait_count
                best_value = value_ev
                best_tile = tile

        return best_tile

    def _should_declare_riichi(self, game_view: GameView,
                               riichi_candidates: List[Tile]) -> bool:
        """Determine whether to declare Riichi or stay Dama (silent tenpai).

        Strategic guidelines:
        - Declare Riichi for good waits to gain Han, Ippatsu, Ura-dora.
        - Stay Dama if hand already has Haneman+ value (5+ Dora).
        - Stay Dama for very bad single-tile waits in late game with 0 dora.
        - Weigh rank situation: 4th place should push harder; 1st place can stay dama.
        """
        from mahjong.rules.agari import get_waiting_tiles

        hand = game_view.my_hand
        current_34 = hand.to_34_array()
        features = self._extract_features(game_view)
        visible = features.visible_34
        dora_indices = features.dora_indices

        # Evaluate best wait among candidates
        best_waits = 0
        best_wait_quality = 0  # 0=tanki, 1=penchan/kanchan, 2=ryanmen/shanpon
        for tile in riichi_candidates:
            test_34 = list(current_34)
            test_34[tile.index34] -= 1
            waits = get_waiting_tiles(test_34)
            wait_count = self._wait_count_after_discard(
                visible, tile, waits
            )
            best_waits = max(best_waits, wait_count)

            # Classify wait quality
            wq = self._wait_quality(test_34, waits)
            best_wait_quality = max(best_wait_quality, wq)

        # Dora count in hand
        dora_count = sum(current_34[i] for i in dora_indices if i < 34)
        dora_count += sum(1 for t in hand.closed_tiles if t.is_red)

        # 1. Late-game single-tile tanki with 0 dora: stay dama
        if game_view.remaining_tiles <= 10 and best_waits <= 2 and dora_count == 0:
            return False

        # 2. Opponent Riichi + very bad wait (≤ 1 tile) + 0 dora: stay dama
        if any(opp.is_riichi for opp in game_view.opponents) and best_waits <= 1 and dora_count == 0:
            return False

        # 3. Already huge Dama value (Haneman+ without riichi) and good position
        if dora_count >= 5 and not game_view.is_dealer:
            # In 4th place: always declare riichi for comeback potential
            my_rank = self._my_rank(game_view)
            if my_rank < 3:  # Not last place — stay dama on big hands
                return False

        # 4. Rank-awareness in All-Last: 1st place stay dama on bad waits,
        #    4th place push aggressively even with mediocre waits
        if "南4" in game_view.round_label or "West4" in game_view.round_label or game_view.remaining_tiles <= 14:
            my_rank = self._my_rank(game_view)
            if my_rank == 0 and best_wait_quality == 0 and dora_count == 0:
                # 1st place, tanki only, no dora: stay dama (protect 1st)
                return False

        return True

    def _wait_quality(self, tenpai_34: List[int], waits: List[int]) -> int:
        """Classify wait quality: 2=ryanmen/shanpon, 1=kanchan/penchan, 0=tanki."""
        if len(waits) >= 2:
            # Check if it's a two-sided sequence wait (ryanmen) or shanpon
            # Shanpon: two different tiles waiting = at least 2 distinct indices
            return 2
        if len(waits) == 1:
            w = waits[0]
            if w < 27:  # Numbered tile
                num = (w % 9) + 1
                # Penchan: 3-tile wait on 1-2 (waiting 3) or 8-9 (waiting 7)
                if num in (3, 7):
                    # Check if this is a penchan (1-2 wait 3 or 7-8 wait 9)
                    suit_base = (w // 9) * 9
                    if (w + 1 < 34 and tenpai_34[w + 1] > 0 and
                            w + 2 < 34 and tenpai_34[w + 2] > 0):
                        return 1  # Could be kanchan or penchan
                    if (w - 1 >= 0 and tenpai_34[w - 1] > 0 and
                            w - 2 >= 0 and tenpai_34[w - 2] > 0):
                        return 1
                return 0  # Tanki or isolated
        return 0

    def _my_rank(self, game_view: GameView) -> int:
        """Return 0=1st, 1=2nd, 2=3rd, 3=4th based on current scores."""
        scores = [(opp.score, opp.seat) for opp in game_view.opponents]
        scores.append((game_view.my_score, game_view.my_seat))
        scores.sort(key=lambda x: -x[0])
        for rank, (score, seat) in enumerate(scores):
            if seat == game_view.my_seat:
                return rank
        return 0

    def _hand_dora_count(self, game_view: GameView) -> int:
        """Count visible dora in our hand, including red fives and kita."""
        is_sanma = len(game_view.opponents) == 2
        dora_indices = {
            next_tile_index(tile.index34, is_sanma)
            for tile in game_view.dora_indicators
        }
        counts = game_view.my_hand.to_34_array()
        dora_count = sum(counts[i] for i in dora_indices if i < 34)
        dora_count += sum(1 for t in game_view.my_hand.closed_tiles if t.is_red)
        dora_count += game_view.my_kita_count if is_sanma else 0
        return dora_count

    def _has_value_pair(self, game_view: GameView) -> bool:
        """Whether the hand contains a pair that can provide a yaku."""
        counts = game_view.my_hand.to_34_array()
        value_tiles = {
            game_view.my_wind.index34,
            game_view.round_wind.index34,
            31, 32, 33,
        }
        return any(counts[i] >= 2 for i in value_tiles)

    def _should_push_against_riichi(self, game_view: GameView,
                                    current_shanten: int) -> bool:
        """Make a bounded push/fold decision before switching to defense.

        This is intentionally an estimate rather than a full point-EV model:
        strong value, dealer advantage, and comeback pressure can justify
        pushing, while late or dealer-riichi situations remain conservative.
        """
        if current_shanten > 2 or game_view.remaining_tiles < 10:
            return False

        dora_count = self._hand_dora_count(game_view)
        value_score = dora_count * 2
        value_score += 2 if self._has_value_pair(game_view) else 0
        value_score += 2 if game_view.is_dealer else 0
        value_score += 1 if game_view.my_hand.is_menzen else 0

        riichi_dealer = any(
            opp.is_riichi and opp.is_dealer for opp in game_view.opponents
        )
        if riichi_dealer:
            value_score -= 2

        if current_shanten == 1:
            # A normal 1-shanten value hand may continue; a weak hand folds.
            if value_score >= 4 and game_view.remaining_tiles >= 14:
                return True
            # Last place has to accept more variance for a realistic comeback.
            return (self._my_rank(game_view) == 3 and value_score >= 2
                    and game_view.remaining_tiles >= 24 and not riichi_dealer)

        # 2-shanten pushes are reserved for an early, dealer, high-value hand.
        return (current_shanten == 2 and game_view.is_dealer
                and value_score >= 8 and game_view.remaining_tiles >= 36
                and not riichi_dealer)

    def _estimate_tenpai_value(self, game_view: GameView, hand,
                               discard_tile: Tile, tenpai_34: List[int],
                               waits: List[int], visible: List[int]) -> int:
        """Estimate average ron value for a tenpai discard.

        Each wait is scored through the normal rules engine with riichi
        enabled, then weighted by the number of copies still live.  This is a
        bounded calculation performed only for minimum-shanten tenpai
        candidates, so it improves value selection without turning every
        discard into a search tree.
        """
        if not waits:
            return 0

        from mahjong.rules.scoring import calculate_score

        base_hand = hand.clone()
        try:
            base_hand.closed_tiles.remove(discard_tile)
        except ValueError:
            return 0
        base_hand.draw_tile = None

        dora_tiles = self._dora_indices_for_scoring(game_view)
        is_sanma = len(game_view.opponents) == 2

        weighted_points = 0
        live_waits = 0
        for wait in waits:
            remaining = max(0, 4 - visible[wait])
            if remaining <= 0:
                continue
            winning_hand = base_hand.clone()
            win_tile = ALL_TILES_136[wait * 4 + 1]
            winning_hand.closed_tiles.append(win_tile)
            result = calculate_score(
                winning_hand,
                win_tile=win_tile,
                is_tsumo=False,
                seat_wind_34=game_view.my_wind.index34,
                round_wind_34=game_view.round_wind.index34,
                is_dealer=game_view.is_dealer,
                dora_tiles_34=dora_tiles,
                uradora_tiles_34=[],
                honba=game_view.honba,
                is_riichi=True,
                is_sanma=is_sanma,
                kita_count=game_view.my_kita_count,
            )
            if result is not None:
                weighted_points += remaining * result.total_points
                live_waits += remaining

        if live_waits:
            return weighted_points // live_waits

        # No legal yaku/value route was found; retain a small fallback so the
        # value field remains deterministic for unusual/open tenpai shapes.
        return self._hand_dora_count(game_view) * 100

    def rank_discards(self, game_view: GameView,
                      available_discards: List[Tile]) -> List[Tile]:
        """Rank all available discards from most recommended to least recommended."""
        hand = game_view.my_hand
        self._current_opponents = game_view.opponents
        self._current_dora_indicators = game_view.dora_indicators
        self._current_game_view = game_view

        try:
            if self._should_defend(game_view, available_discards):
                # Defending: sort by danger score (safest first)
                ranked_safe = self._rank_safe_tiles(game_view, available_discards)
                if ranked_safe:
                    return ranked_safe

            return self._rank_discards_for_shanten(
                hand, available_discards, game_view.dora_indicators,
                is_sanma=len(game_view.opponents) == 2,
                my_wind=game_view.my_wind,
                round_wind=game_view.round_wind)
        finally:
            self._current_opponents = []
            self._current_dora_indicators = []
            self._current_game_view = None

    def _best_discard_for_shanten(self, hand, available: List[Tile],
                                  dora_indicators=None,
                                  is_sanma: bool = False,
                                  my_wind: Optional[Wind] = None,
                                  round_wind: Optional[Wind] = None) -> Tile:
        """Find the discard that minimizes shanten."""
        ranked = self._rank_discards_for_shanten(
            hand, available, dora_indicators, is_sanma=is_sanma,
            my_wind=my_wind, round_wind=round_wind
        )
        return ranked[0]

    def _detect_route_potentials(self, current_34: List[int], melds: List[Meld], is_sanma: bool) -> dict:
        """Detect potential strategic value routes (Honitsu, Tanyao, Chiitoitsu, Kokushi)."""
        routes = {
            "honitsu_suit": -1,
            "tanyao_bias": False,
            "special_route": None,
        }
        # 1. Flush (Honitsu/Chinitsu) check
        suit_counts = [
            sum(current_34[s * 9 : s * 9 + 9])
            for s in range(3)
        ]
        honor_count = sum(current_34[27:34])
        for s in range(3):
            # In sanma or heavy suit concentration: >= 7 tiles with >= 5 suit tiles
            min_flush_tiles = 7 if (is_sanma or suit_counts[s] >= 6) else 8
            if suit_counts[s] + honor_count >= min_flush_tiles and suit_counts[s] >= 5:
                routes["honitsu_suit"] = s
                break

        # 2. Tanyao check (few or zero terminals/honors)
        yaochu_count = sum(current_34[i] for i in YAOCHU_INDICES)
        if yaochu_count <= 2 and routes["honitsu_suit"] == -1:
            routes["tanyao_bias"] = True

        # 3. Special routes (Chiitoitsu & Kokushi)
        if not melds and sum(current_34) in (13, 14):
            standard_s = shanten_standard(list(current_34))
            chiitoi_s = shanten_chiitoi(list(current_34))
            kokushi_s = shanten_kokushi(list(current_34))
            current_s = min(standard_s, chiitoi_s, kokushi_s)
            if chiitoi_s == current_s and chiitoi_s <= standard_s + 1:
                routes["special_route"] = "chiitoi"
            elif kokushi_s == current_s and kokushi_s <= standard_s + 1:
                routes["special_route"] = "kokushi"

        return routes

    def _rank_discards_for_shanten(self, hand, available: List[Tile],
                                   dora_indicators=None,
                                   is_sanma: bool = False,
                                   my_wind: Optional[Wind] = None,
                                   round_wind: Optional[Wind] = None) -> List[Tile]:
        """Rank all available discards by shanten, ukeire, value routes, and shape priority."""
        current_34 = hand.to_34_array()
        current_view = getattr(self, "_current_game_view", None)

        if current_view is not None:
            features = self._extract_features(current_view)
            visible = features.visible_34
            dora_indices = features.dora_indices
        else:
            visible = list(current_34)
            for tile in hand.discard_pool:
                visible[tile.index34] += 1
            for meld in hand.melds:
                for tile in meld.tiles:
                    visible[tile.index34] += 1
            dora_indices = set()
            for indicator in dora_indicators or []:
                dora_indices.add(next_tile_index(indicator.index34, is_sanma))

        # 5-Block analysis: detect surplus floating tiles
        block_count, floating_indices = self._analyze_blocks(current_34)
        current_shanten = shanten(current_34)

        # Route-aware analysis (Honitsu, Tanyao, Chiitoitsu, Kokushi)
        routes = self._detect_route_potentials(current_34, hand.melds, is_sanma)

        # Defensive tie-breaker under Riichi
        safety_rank = {}
        if current_view and any(opp.is_riichi for opp in current_view.opponents):
            for rank, safe_tile in enumerate(self._rank_safe_tiles(current_view, available)):
                safety_rank.setdefault(safe_tile.index34, rank)

        # 1. Fast preliminary evaluation (shanten, routes, and priority)
        seen = set()
        prelim = []
        for tile in available:
            key = (tile.index34, tile.is_red)
            if key in seen:
                continue
            seen.add(key)

            test_34 = list(current_34)
            test_34[tile.index34] -= 1
            s = shanten(test_34)

            # Refined opening & mid-game discard priority
            priority = self._tile_discard_priority(
                tile, current_34, visible, my_wind=my_wind,
                round_wind=round_wind, is_sanma=is_sanma,
                strategic_shanten=current_shanten
            )
            # 5-Block theory bonus: if >= 5 blocks exist, discard surplus floaters first
            if block_count >= 5 and tile.index34 in floating_indices:
                priority -= 4

            # Route-aware adjustments
            if routes["honitsu_suit"] != -1:
                suit_val = tile.index34 // 9 if tile.index34 < 27 else -1
                if suit_val != -1 and suit_val != routes["honitsu_suit"]:
                    priority -= 7
                elif suit_val == routes["honitsu_suit"] or tile.is_honor:
                    priority += 4
            elif routes["tanyao_bias"]:
                if tile.is_yaochu:
                    priority -= 5
                else:
                    priority += 2

            if routes["special_route"] == "chiitoi":
                if current_34[tile.index34] == 2:
                    priority += 8
                elif current_34[tile.index34] >= 3:
                    priority -= 2
            elif routes["special_route"] == "kokushi":
                if tile.index34 not in YAOCHU_INDICES:
                    priority -= 8
                elif current_34[tile.index34] >= 2:
                    priority += 5

            if safety_rank:
                # A lower rank means safer.  Keep this influence bounded so
                # shape/ukeire still dominate, but let safety break close
                # offensive ties.
                priority += min(8, safety_rank.get(tile.index34, len(available)) // 2)

            if tile.index34 in dora_indices:
                priority += 12
            priority -= self._value_shape_bonus(test_34, dora_indices)
            prelim.append((tile, test_34, s, priority))

        if not prelim:
            return list(available)

        min_s = min(item[2] for item in prelim)

        # 2. Compute ukeire for optimal candidates.
        ukeire_by_key = {}
        for tile, test_34, s, _priority in prelim:
            if s == min_s and s <= 3:
                post_visible = self._visible_after_discard(visible, tile)
                ukeire_by_key[(tile.index34, tile.is_red)] = _cached_ukeire(
                    tuple(test_34), post_visible, s, is_sanma
                )
        continuation_keys = set()
        if (min_s == 2 and block_count >= 5 and
                (current_view is None or current_view.remaining_tiles >= 60)):
            immediate = sorted(
                ((ukeire, priority, tile.index34, tile.is_red, test_34)
                 for tile, test_34, s, priority in prelim
                 if s == min_s
                 for ukeire in [ukeire_by_key[(tile.index34, tile.is_red)]]),
                key=lambda item: (-item[0], item[1]),
            )
            if immediate:
                best_immediate = immediate[0][0]
                tied_best = [item for item in immediate if item[0] == best_immediate]
                if len(tied_best) >= 2:
                    continuation_keys = {
                        (idx, is_red)
                        for _u, _p, idx, is_red, _shape in tied_best[:2]
                    }

        evaluated = []
        for tile, test_34, s, priority in prelim:
            key = (tile.index34, tile.is_red)
            ukeire = ukeire_by_key.get(key, 0)
            continuation = 0
            wait_quality = 0
            value_ev = 0
            if key in continuation_keys:
                continuation = self._continuation_ukeire(
                    tuple(test_34),
                    self._visible_after_discard(visible, tile),
                    s,
                    is_sanma,
                )
            # At tenpai (0-shanten): rank by wait quality and estimated value.
            if s == min_s == 0:
                from mahjong.rules.agari import get_waiting_tiles
                waits = get_waiting_tiles(test_34)
                wait_quality = self._wait_quality(test_34, waits)
                value_ev = self._estimate_tenpai_value(
                    current_view, hand, tile, test_34, waits, visible
                ) if current_view else 0

            # Sort tuple: shanten, ukeire, continuation, value, wait quality, priority.
            evaluated.append(
                ((s, -ukeire, -continuation, -value_ev, -wait_quality, priority), tile)
            )

        evaluated.sort(key=lambda item: item[0])
        return [item[1] for item in evaluated]

    def _analyze_blocks(self, counts_34: List[int]) -> Tuple[int, set]:
        """Estimate block count and identify surplus floating tile indices."""
        block_count = 0
        in_block = [False] * 34

        # Triplets
        for i in range(34):
            if counts_34[i] >= 3:
                block_count += 1
                in_block[i] = True

        # Pairs
        for i in range(34):
            if counts_34[i] == 2 and not in_block[i]:
                block_count += 1
                in_block[i] = True

        # Numbered sequences and protoruns
        for suit in range(3):
            base = suit * 9
            # Sequences
            for n in range(7):
                if (counts_34[base + n] > 0 and 
                    counts_34[base + n + 1] > 0 and 
                    counts_34[base + n + 2] > 0):
                    block_count += 1
                    in_block[base + n] = True
                    in_block[base + n + 1] = True
                    in_block[base + n + 2] = True

            # Open protoruns (ryanmen)
            for n in range(8):
                if counts_34[base + n] > 0 and counts_34[base + n + 1] > 0:
                    if not (in_block[base + n] and in_block[base + n + 1]):
                        block_count += 1
                        in_block[base + n] = True
                        in_block[base + n + 1] = True

            # Closed protoruns (kanchan)
            for n in range(7):
                if counts_34[base + n] > 0 and counts_34[base + n + 2] > 0:
                    if not (in_block[base + n] and in_block[base + n + 2]):
                        block_count += 1
                        in_block[base + n] = True
                        in_block[base + n + 2] = True

        floating = {i for i in range(34) if counts_34[i] > 0 and not in_block[i]}
        return block_count, floating

    def _value_shape_bonus(self, counts: List[int], dora_indices: set[int]) -> int:
        """Estimate value retained by a post-discard hand shape."""
        bonus = sum(min(2, counts[i]) for i in dora_indices if i < 34) * 3
        honors = sum(counts[27:34])
        suits = {i // 9 for i, n in enumerate(counts[:27]) if n}
        if len(suits) == 1:
            bonus += 2 if honors else 3  # honitsu / chinitsu potential
        yaochu = {0, 8, 9, 17, 18, 26, *range(27, 34)}
        if not any(counts[i] for i in yaochu):
            bonus += 2  # tanyao potential
        return bonus

    def _tile_discard_priority(self, tile: Tile, current_34: List[int],
                               visible: List[int],
                               my_wind: Optional[Wind] = None,
                               round_wind: Optional[Wind] = None,
                               is_sanma: bool = False,
                               strategic_shanten: Optional[int] = None) -> int:
        """Lower = more recommended to discard.
        
        Refined master-level 14-tier tactical hierarchy:
        """
        if tile.is_red:
            return 35  # Strongly preserve red dora

        idx = tile.index34
        in_hand = current_34[idx]
        seen_count = visible[idx]

        # Sanma North
        if is_sanma and idx == 30:
            if in_hand >= 3:
                return 18
            if in_hand == 2:
                return 15
            return 12 if seen_count < 2 else 7

        # Honors (index 27..33)
        if tile.is_honor:
            yakuhai_indices = {31, 32, 33}  # White, Green, Red Dragons
            if my_wind is not None:
                yakuhai_indices.add(my_wind.index34)
            if round_wind is not None:
                yakuhai_indices.add(round_wind.index34)

            is_yakuhai = idx in yakuhai_indices

            if in_hand >= 3:
                return 22 if is_yakuhai else 18
            if in_hand == 2:
                return 20 if is_yakuhai else 14

            # Isolated honor (1 in hand)
            if not is_yakuhai:
                # Guest wind (Otakaze - 客风)
                if seen_count >= 2:
                    return -10  # 2+ seen (dead guest wind): absolute #1 discard
                elif seen_count == 1:
                    return -7   # 1-seen guest wind: discard immediately
                else:
                    return -3   # Fresh 0-seen guest wind
            else:
                # Yakuhai (Dragons, Seat/Round wind - 役牌)
                if seen_count >= 3:
                    return -9   # 3 seen: dead yakuhai, discard immediately
                elif seen_count == 2:
                    return -4   # 2 seen: only 1 left, discard early
                elif seen_count == 1:
                    return 3    # 1 seen: 2 left, hold for value pair
                else:
                    return 6    # Fresh 0-seen: hold for value potential

        # Numbered tiles (0..26)
        num = (idx % 9) + 1  # 1 to 9

        # Connectivity in hand
        has_triplet = in_hand >= 3
        has_pair = in_hand == 2
        has_adjacent = False
        has_gap = False

        if num > 1 and current_34[idx - 1] > 0:
            has_adjacent = True
        if num < 9 and current_34[idx + 1] > 0:
            has_adjacent = True
        if num > 2 and current_34[idx - 2] > 0:
            has_gap = True
        if num < 8 and current_34[idx + 2] > 0:
            has_gap = True

        if has_triplet:
            return 24
        if has_pair:
            return 16 if num in (2, 3, 4, 5, 6, 7, 8) else 13

        if has_adjacent:
            # Connected open or side protorun (e.g. 4-5, 2-3, 1-2, 8-9)
            if num in (1, 9):
                return 10  # Penchan (1-2 or 8-9)
            elif num in (2, 8):
                return 14  # Side ryanmen (2-3 or 7-8)
            else:
                return 17  # Central ryanmen (3-4, 4-5, 5-6, 6-7)
        elif has_gap:
            # Kanchan protorun (e.g. 1-3, 7-9, 2-4, 4-6)
            if num in (1, 9):
                return 8   # Terminal kanchan (1-3, 7-9)
            elif num in (2, 8):
                return 10  # Inner kanchan (2-4, 6-8)
            else:
                return 12  # Central kanchan (3-5, 4-6, 5-7)
        else:
            # Isolated floater
            if num in (1, 9):
                if seen_count >= 2:
                    return -6  # Dead terminal
                elif seen_count == 1:
                    return -1
                return 1       # Fresh isolated terminal
            elif num in (2, 8):
                if seen_count >= 2:
                    return 2
                return 5       # Isolated 2/8
            else:
                return 8       # Isolated 3..7

    def _should_defend(self, game_view: GameView,
                       available_discards: Optional[List[Tile]] = None) -> bool:
        """Check if we should switch to defensive play."""
        hand = game_view.my_hand
        current_shanten = shanten(hand.to_34_array())
        riichi_opponents = [opp for opp in game_view.opponents if opp.is_riichi]

        if not riichi_opponents:
            return False

        # Use the bounded push/fold model for 1- and 2-shanten hands.  Very
        # distant hands always fold because their chance of reaching tenpai
        # before the hand ends is too small to justify deal-in risk.
        if current_shanten >= 2:
            return not self._should_push_against_riichi(game_view, current_shanten)

        if current_shanten == 1:
            return not self._should_push_against_riichi(game_view, current_shanten)

        return False

    def _find_safe_tile(self, game_view: GameView,
                        available: List[Tile]) -> Optional[Tile]:
        """Find the safest tile to discard against Riichi opponents using Suji and Kabe."""
        ranked = self._rank_safe_tiles(game_view, available)
        return ranked[0] if ranked else None

    def _rank_safe_tiles(self, game_view: GameView,
                         available: List[Tile]) -> List[Tile]:
        """Rank available tiles by defensive safety (safest first)."""
        riichi_players = [opp for opp in game_view.opponents if opp.is_riichi]
        if not riichi_players:
            return list(available)

        features = self._extract_features(game_view)
        visible_34 = features.visible_34
        common_genbutsu = features.common_genbutsu
        genbutsu_by_seat = features.genbutsu_by_seat
        suji_safe_tiles = features.suji_safe
        tedashi_suji = features.tedashi_suji
        kabe_no_chance = features.kabe_no_chance
        kabe_one_chance = features.kabe_one_chance
        dora_indices = features.dora_indices

        def tile_danger(tile: Tile) -> int:
            idx = tile.index34
            seen = visible_34[idx]
            is_dora = (idx in dora_indices) or tile.is_red

            # 1. 100% Genbutsu (in riichi player's discard pool = 100% safe)
            if idx in common_genbutsu:
                return 0
            # Safe against one riichi player is not safe against another.
            # Keep it near the top of the list, but below tiles safe to all.
            if any(idx in g_set for g_set in genbutsu_by_seat.values()):
                return 2

            base_danger = 20
            if tile.is_honor:
                if seen >= 4:
                    base_danger = 0  # 100% safe
                elif seen == 3:
                    base_danger = 2  # 3 visible
                elif seen == 2:
                    base_danger = 5  # 2 visible
                elif seen == 1:
                    base_danger = 10
                else:
                    base_danger = 14 # Live honor (生牌)
            else:
                num = (idx % 9) + 1
                if idx in kabe_no_chance:
                    base_danger = 3  # Kabe No-Chance
                elif idx in tedashi_suji and num in (1, 9):
                    base_danger = 3  # Tedashi suji 1/9: very reliable
                elif idx in suji_safe_tiles and num in (1, 9):
                    base_danger = 4  # Suji 1 or 9 (表筋)
                elif idx in tedashi_suji and num in (2, 8):
                    base_danger = 5  # Tedashi suji 2/8
                elif idx in suji_safe_tiles and num in (2, 8):
                    base_danger = 6  # Suji 2 or 8
                elif idx in tedashi_suji:
                    base_danger = 7  # Tedashi suji middle
                elif idx in suji_safe_tiles:
                    base_danger = 8  # Regular suji middle
                elif idx in kabe_one_chance:
                    base_danger = 9  # Kabe One-Chance (not guaranteed safe)
                elif num in (1, 9):
                    base_danger = 11 # Unsafe 1, 9
                elif num in (2, 8):
                    base_danger = 13 # Unsafe 2, 8
                else:
                    base_danger = 16 # Unsafe middle tile

            if is_dora:
                base_danger += 6
            return base_danger

        return sorted(available, key=tile_danger)

    def _should_call_pon(self, game_view: GameView, meld: Meld) -> bool:
        """Decide whether to call pon."""
        hand = game_view.my_hand

        # Don't call if defending
        if self._should_defend(game_view):
            return False

        # Don't call pon on isolated guest winds in a well-formed closed hand.
        # Calling guest wind pon sacrifices menzen/riichi for a single-han open hand
        # that is easy to read and hard to complete. Only call if we're already
        # committed to an open strategy (hand already has melds) or very far from riichi.
        idx = meld.tile_index34
        is_guest_wind = (27 <= idx <= 30)  # Any wind tile
        my_wind_idx = game_view.my_wind.index34
        round_wind_idx = game_view.round_wind.index34
        is_my_yakuhai = (idx in {my_wind_idx, round_wind_idx, 31, 32, 33})
        if is_guest_wind and not is_my_yakuhai and not hand.melds:
            return False  # Never open for otakaze pon in closed hand

        # Calculate shanten before and after
        current_34 = hand.to_34_array()
        current_s = shanten(current_34)

        # Simulate: remove 2 tiles for pon, reduce closed count
        test_34 = list(current_34)
        test_34[idx] -= 2

        # After pon, we need to discard, find best
        new_s = 99
        for i in range(34):
            if test_34[i] > 0:
                test_34[i] -= 1
                s = shanten(test_34)
                new_s = min(new_s, s)
                test_34[i] += 1

        # Never open a hand purely for a one-shanten gain if it has no
        # credible yaku/value route. This avoids the visibly weak habit of
        # calling random honors and ending in an unriichiable no-yaku hand.
        return (new_s < current_s and current_s <= 2
                and self._call_has_value(game_view, idx))

    def _should_call_chi(self, game_view: GameView, meld: Meld) -> bool:
        """Decide whether to call chi."""
        hand = game_view.my_hand

        if self._should_defend(game_view):
            return False

        current_34 = hand.to_34_array()
        current_s = shanten(current_34)

        # Simulate chi
        test_34 = list(current_34)
        for t in meld.tiles:
            if t != meld.called_tile:
                test_34[t.index34] -= 1

        new_s = 99
        for i in range(34):
            if test_34[i] > 0:
                test_34[i] -= 1
                s = shanten(test_34)
                new_s = min(new_s, s)
                test_34[i] += 1

        return (new_s < current_s and current_s <= 2
                and self._call_has_value(game_view, meld.tile_index34))

    def _call_has_value(self, game_view: GameView, called_index: int) -> bool:
        """Conservative yaku gate for open calls."""
        counts = game_view.my_hand.to_34_array()
        counts[called_index] += 1
        # Any honor triplet can become yakuhai; avoid opening unrelated tiles.
        if called_index >= 27:
            return called_index in {
                game_view.my_wind.index34, game_view.round_wind.index34,
                31, 32, 33,
            }
        # Tanyao route: all current tiles are simples.
        yaochu = {0, 8, 9, 17, 18, 26, *range(27, 34)}
        if not any(counts[i] for i in yaochu):
            return True
        # Honitsu/chinitsu route: one numbered suit dominates the hand.
        suits = {i // 9 for i, n in enumerate(counts[:27]) if n}
        return len(suits) == 1


# Baseline alias pointing to the original, un-refactored GreedyAI
BaselineGreedyAI = GreedyAI


class RefinedGreedyAI(GreedyAI):
    """Experimental defense upgrade candidate under benchmark verification.

    Only promoted to replace GreedyAI if benchmark empirically proves superiority.
    """

    def _extract_features(self, game_view: GameView) -> PublicFeatures:
        """Use the canonical public feature representation."""
        return super()._extract_features(game_view)

    def _dora_indices_for_scoring(self, game_view: GameView) -> List[int]:
        """Return actual dora indices, including Sanma indicator rules."""
        is_sanma = len(game_view.opponents) == 2
        return [
            next_tile_index(tile.index34, is_sanma)
            for tile in game_view.dora_indicators
        ]

    def _visible_after_discard(self, visible: List[int],
                               discard_tile: Tile) -> tuple[int, ...]:
        """Remove the candidate discard from public visibility."""
        result = list(visible)
        result[discard_tile.index34] = max(
            0, result[discard_tile.index34] - 1
        )
        return tuple(result)

    def _wait_count_after_discard(self, visible: List[int],
                                  discard_tile: Tile,
                                  waits: List[int]) -> int:
        post_visible = self._visible_after_discard(visible, discard_tile)
        return sum(max(0, 4 - post_visible[wait]) for wait in waits)

    def _riichi_tie_prefers_tile(self, tile: Tile, best_tile: Tile,
                                 dora_indices: set[int]) -> bool:
        """Prefer retaining dora when all other riichi metrics tie."""
        return (
            tile.index34 not in dora_indices
            and best_tile.index34 in dora_indices
        )

    def _continuation_ukeire(self, counts: tuple[int, ...],
                             visible: tuple[int, ...],
                             current_shanten: int,
                             is_sanma: bool = False) -> int:
        return _cached_refined_continuation_ukeire(
            counts, visible, current_shanten, is_sanma
        )

    def _hand_dora_count(self, game_view: GameView) -> int:
        """Count dora across closed tiles, melds, red tiles, and kita."""
        is_sanma = len(game_view.opponents) == 2
        dora_indices = set(self._dora_indices_for_scoring(game_view))
        all_tiles = list(game_view.my_hand.closed_tiles)
        for meld in game_view.my_hand.melds:
            all_tiles.extend(meld.tiles)
        counts = tiles_to_34_array(all_tiles)
        dora_count = sum(counts[i] for i in dora_indices if i < 34)
        dora_count += sum(1 for tile in all_tiles if tile.is_red)
        dora_count += game_view.my_kita_count if is_sanma else 0
        return dora_count

    def _has_valid_open_win(
            self, game_view: GameView,
            candidates: List[Tuple[Tile, List[int]]]) -> bool:
        """Require an open hand to have at least one actual scoring route."""
        hand = game_view.my_hand
        if hand.is_menzen:
            return True

        from mahjong.rules.scoring import calculate_score

        dora_tiles = self._dora_indices_for_scoring(game_view)
        is_sanma = len(game_view.opponents) == 2
        for discard_tile, waits in candidates:
            base_hand = hand.clone()
            try:
                base_hand.closed_tiles.remove(discard_tile)
            except ValueError:
                continue
            base_hand.draw_tile = None
            for wait in waits:
                winning_hand = base_hand.clone()
                win_tile = ALL_TILES_136[wait * 4 + 1]
                winning_hand.closed_tiles.append(win_tile)
                result = calculate_score(
                    winning_hand,
                    win_tile=win_tile,
                    is_tsumo=False,
                    seat_wind_34=game_view.my_wind.index34,
                    round_wind_34=game_view.round_wind.index34,
                    is_dealer=game_view.is_dealer,
                    dora_tiles_34=dora_tiles,
                    uradora_tiles_34=[],
                    honba=game_view.honba,
                    is_riichi=hand.is_riichi,
                    is_double_riichi=hand.is_double_riichi,
                    is_ippatsu=False,
                    is_sanma=is_sanma,
                    kita_count=game_view.my_kita_count,
                )
                if result is not None:
                    return True
        return False

    def _should_defend(self, game_view: GameView,
                       available_discards: Optional[List[Tile]] = None) -> bool:
        hand = game_view.my_hand
        current_shanten = shanten(hand.to_34_array())
        riichi_opponents = [opp for opp in game_view.opponents if opp.is_riichi]
        if not riichi_opponents:
            return False

        if current_shanten >= 2:
            return not self._should_push_against_riichi(game_view, current_shanten)
        if current_shanten == 1:
            return not self._should_push_against_riichi(game_view, current_shanten)
        if current_shanten == 0:
            return not self._should_push_on_tenpai(game_view, available_discards)
        return False

    def _should_push_on_tenpai(self, game_view: GameView,
                               available_discards: Optional[List[Tile]] = None) -> bool:
        riichi_opponents = [opp for opp in game_view.opponents if opp.is_riichi]
        if not riichi_opponents:
            return True

        if available_discards is None:
            available_discards = game_view.my_hand.closed_tiles

        hand = game_view.my_hand
        current_34 = hand.to_34_array()
        tenpai_discards = []
        for tile in available_discards:
            test_34 = list(current_34)
            test_34[tile.index34] -= 1
            if shanten(test_34) == 0:
                tenpai_discards.append(tile)

        if not tenpai_discards:
            return True

        from mahjong.rules.agari import get_waiting_tiles
        features = self._extract_features(game_view)
        visible = features.visible_34
        candidate_metrics = []
        for tile in tenpai_discards:
            test_34 = list(current_34)
            test_34[tile.index34] -= 1
            waits = get_waiting_tiles(test_34)
            live_waits = self._wait_count_after_discard(
                visible, tile, waits
            )
            danger = self._tile_danger_score(tile, game_view, features)
            candidate_metrics.append((
                tile, waits, self._wait_quality(test_34, waits),
                live_waits, danger,
            ))

        if not hand.is_menzen and not self._has_valid_open_win(
                game_view, [(tile, waits) for tile, waits, *_ in candidate_metrics]):
            return False

        dealer_riichi = any(opp.is_dealer for opp in riichi_opponents)
        dora_count = self._hand_dora_count(game_view)
        if dora_count >= 2 or game_view.is_dealer:
            return True

        ranked_safe = self._rank_safe_tiles(game_view, available_discards)
        if not ranked_safe:
            return True

        safest_danger = self._tile_danger_score(
            ranked_safe[0], game_view, features
        )
        min_tenpai_danger = min(item[4] for item in candidate_metrics)

        good_candidates = [
            item for item in candidate_metrics
            if item[2] >= 2 and item[3] >= 3
        ]
        if good_candidates:
            best_good = max(
                good_candidates, key=lambda item: (item[3], -item[4])
            )
            extreme_risk = (
                dealer_riichi
                and game_view.remaining_tiles <= 10
                and dora_count == 0
                and best_good[4] >= 17
                and safest_danger <= 1
            )
            return not extreme_risk

        if dora_count <= 1 and min_tenpai_danger >= 17 and safest_danger <= 2:
            num_safe = sum(
                1 for tile in available_discards
                if self._tile_danger_score(tile, game_view, features) <= 2
            )
            can_sustain = num_safe >= 2 or game_view.remaining_tiles <= 12
            if (can_sustain and game_view.remaining_tiles <= 16
                    and (dealer_riichi or game_view.remaining_tiles <= 14)):
                return False

        return True
    def _tile_danger_score(self, tile: Tile, game_view: GameView,
                           features: Optional[PublicFeatures] = None) -> int:
        if features is None:
            features = self._extract_features(game_view)
        idx = tile.index34
        seen = features.visible_34[idx]
        is_dora = (idx in features.dora_indices) or tile.is_red

        if idx in features.common_genbutsu:
            return 0
        if any(idx in g_set for g_set in features.genbutsu_by_seat.values()):
            return 2

        base_danger = 20
        if tile.is_honor:
            in_hand = game_view.my_hand.to_34_array()[idx]
            seen_on_table = max(0, seen - in_hand)
            unseen = max(0, 4 - seen)

            yakuhai_indices = {31, 32, 33}
            if game_view.round_wind:
                yakuhai_indices.add(game_view.round_wind.index34)
            for opp in game_view.opponents:
                if opp.is_riichi or opp.is_dealer:
                    yakuhai_indices.add(opp.seat_wind.index34)
            is_yakuhai = (idx in yakuhai_indices)

            if seen >= 4:
                return 1 if features.kokushi_possible else 0
            elif unseen <= 1:
                if seen_on_table >= 2:
                    base_danger = 1
                elif seen_on_table == 1:
                    base_danger = 2
                else:
                    base_danger = 3 if is_yakuhai else 2
            elif seen_on_table >= 2:
                base_danger = 2 if is_yakuhai else 1
            elif seen_on_table == 1:
                base_danger = 10 if is_yakuhai else 6
            else:
                if in_hand >= 2:
                    base_danger = 13 if is_yakuhai else 7
                else:
                    base_danger = 14 if is_yakuhai else 10
        else:
            num = (idx % 9) + 1
            is_trap = (idx in features.trap_suji)
            is_early_suji = (idx in features.early_suji)

            if idx in features.kabe_no_chance:
                base_danger = 2
            elif is_early_suji and num in (1, 9):
                base_danger = 3
            elif idx in features.suji_safe and num in (1, 9):
                base_danger = 5 if is_trap else 4
            elif is_early_suji and num in (2, 8):
                base_danger = 5
            elif idx in features.suji_safe and num in (2, 8):
                base_danger = 8 if is_trap else 6
            elif is_early_suji:
                base_danger = 7
            elif idx in features.suji_safe:
                base_danger = 11 if is_trap else 8
            elif idx in features.kabe_one_chance:
                base_danger = 9
            elif num in (1, 9):
                base_danger = 10
            elif num in (2, 8):
                base_danger = 12
            elif num in (3, 7):
                base_danger = 15
            elif num in (4, 6):
                base_danger = 17
            else:
                base_danger = 19

        if is_dora:
            base_danger += 6
        return base_danger

    def _rank_safe_tiles(self, game_view: GameView,
                         available: List[Tile]) -> List[Tile]:
        riichi_players = [opp for opp in game_view.opponents if opp.is_riichi]
        if not riichi_players:
            return list(available)
        features = self._extract_features(game_view)
        return sorted(available, key=lambda t: self._tile_danger_score(t, game_view, features))
