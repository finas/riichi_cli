"""AI Coach & Match Reviewer.

Provides real-time move recommendations, post-turn blunder classification,
and comprehensive game reviews.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional, Tuple

from mahjong.core.tile import Tile
from mahjong.engine.action import Action, ActionType, AvailableActions
from mahjong.player.ai_features import extract_public_features
from mahjong.player.ai_policy import LearnedDiscardPolicy
from mahjong.player.ai_value import ValueAndRiskEstimator
from mahjong.player.base import GameView
from mahjong.player.greedy_ai import GreedyAI, _cached_ukeire
from mahjong.rules.shanten import shanten
from mahjong.player.mjai_player import tile_to_mjai_str
from mahjong.ui.tile_display import tile_to_simple_str


class MoveRating(Enum):
    BEST = "BEST"              # 0 EV loss or identical to AI top choice
    GOOD = "GOOD"              # Small acceptable trade-off (< 200 EV)
    INACCURACY = "INACCURACY"  # Sub-optimal shape choice (-200 to -600 EV)
    MISTAKE = "MISTAKE"        # Clear efficiency/value loss (-600 to -1500 EV)
    BLUNDER = "BLUNDER"        # Dangerous push into Riichi or massive EV drop (> -1500 EV)


@dataclass
class TurnReview:
    """Detailed review of a single player decision."""
    turn_number: int
    player_idx: int
    chosen_action: Action
    best_action: Action
    rating: MoveRating
    score_loss_ev: float
    ukeire_chosen: int
    ukeire_best: int
    shanten_chosen: int
    shanten_best: int
    deal_in_risk_chosen: float
    deal_in_risk_best: float
    explanation: str
    top_alternatives: List[Tuple[str, float]] = field(default_factory=list)

@dataclass
class DiscardCandidateEval:
    tile: Tile
    shanten: int
    ukeire: int
    win_prob: float        # percentage (0.0 - 100.0)
    deal_in_risk: float    # percentage (0.0 - 100.0)
    is_safe: bool = False
    is_dora: bool = False
    note: str = ""
    q_value: Optional[float] = None
    mortal_prob: Optional[float] = None  # percentage (0.0 - 100.0) from Mortal Softmax

@dataclass
class CoachSuggestion:
    best_tile: Optional[Tile]
    best_eval: Optional[DiscardCandidateEval]
    alternatives: List[DiscardCandidateEval] = field(default_factory=list)
    action: Optional[Action] = None
    engine_name: str = "Mortal"
    gemini_advice: Optional[str] = None
    tactical_stance: str = ""
    safe_tiles: List[Tuple[Tile, str]] = field(default_factory=list)

class AICoach:
    """Intelligent coach offering live hints and post-move reviews."""

    def __init__(self, model_path: Optional[str] = None):
        self.greedy = GreedyAI(name="CoachGreedy")
        self.policy = LearnedDiscardPolicy(model_path=model_path)
        self.ev_model = ValueAndRiskEstimator()

    def get_live_hint(self, game_view: GameView, available: AvailableActions) -> List[Tuple[Tile, str]]:
        """Return top 3 discard recommendations with tactical explanations."""
        if not available.can_discard:
            return []

        features = extract_public_features(game_view)
        hand_34 = game_view.my_hand.to_34_array()
        cur_s = shanten(hand_34)
        is_sanma = features.is_sanma

        ranked_tiles = self.greedy.rank_discards(game_view, available.can_discard)
        hints = []

        for rank, tile in enumerate(ranked_tiles[:3]):
            test_34 = list(hand_34)
            test_34[tile.index34] -= 1
            post_s = shanten(test_34)
            ukeire = _cached_ukeire(tuple(test_34), tuple(features.visible_34), post_s, is_sanma)
            risk = self.ev_model.estimate_deal_in_probability(tile, features) * 100

            note = f"Shanten: {post_s} | Ukeire: {ukeire} tiles | Deal-in Risk: {risk:.1f}%"
            if features.riichi_opponents and tile.index34 in features.common_genbutsu:
                note += " (100% Genbutsu Safe)"
            elif tile.is_red or tile.index34 in features.dora_indices:
                note += " (Dora)"

            hints.append((tile, note))

        return hints
    def get_coach_suggestion(
        self,
        game_view: GameView,
        available: AvailableActions,
        external_ai: Optional[Any] = None,
        engine_name: str = "Mortal",
    ) -> Optional[CoachSuggestion]:
        """Return a structured coach recommendation with win and deal-in possibilities."""
        external_action: Optional[Action] = None
        display_engine_name = engine_name
        if external_ai is None:
            display_engine_name = "Greedy fallback"
        if external_ai is not None:
            try:
                external_action = external_ai.choose_action(game_view, available)
            except Exception:
                external_action = None
            if (hasattr(external_ai, "get_last_action_source") and
                    external_ai.get_last_action_source() != "external"):
                display_engine_name = "Greedy fallback"
        gemini_advice: Optional[str] = (
            external_ai.get_last_advice()
            if external_ai is not None and hasattr(external_ai, "get_last_advice")
            else None
        )
        # 1. Winning actions take absolute priority!
        if (external_action is not None and external_action.action_type == ActionType.TSUMO) or available.can_tsumo:
            draw_tile = game_view.my_hand.draw_tile or (available.can_discard[0] if available.can_discard else None)
            eval_info = DiscardCandidateEval(
                tile=draw_tile or Tile(0),
                shanten=0,
                ukeire=0,
                win_prob=100.0,
                deal_in_risk=0.0,
                is_safe=True,
                note="",
            )
            return CoachSuggestion(
                best_tile=None,
                best_eval=eval_info,
                alternatives=[],
                action=Action(ActionType.TSUMO, available.player),
                engine_name=display_engine_name,
                gemini_advice=gemini_advice,
            )

        if (external_action is not None and external_action.action_type == ActionType.RON) or available.can_ron:
            discard_tile = game_view.last_discard
            eval_info = DiscardCandidateEval(
                tile=discard_tile or Tile(0),
                shanten=0,
                ukeire=0,
                win_prob=100.0,
                deal_in_risk=0.0,
                is_safe=True,
                note="",
            )
            return CoachSuggestion(
                best_tile=None,
                best_eval=eval_info,
                alternatives=[],
                action=Action(ActionType.RON, available.player),
                engine_name=display_engine_name,
                gemini_advice=gemini_advice,
            )

        # 2. Call or special actions recommended by engine
        if external_action is not None and external_action.action_type not in (
                ActionType.DISCARD, ActionType.RIICHI, ActionType.SKIP):
            return CoachSuggestion(
                best_tile=None,
                best_eval=None,
                alternatives=[],
                action=external_action,
                engine_name=display_engine_name,
                gemini_advice=gemini_advice,
            )

        if not available.can_discard and not available.can_riichi:
            return None

        features = extract_public_features(game_view)
        hand_34 = game_view.my_hand.to_34_array()
        is_sanma = features.is_sanma

        best_tile: Optional[Tile] = None
        if external_action is not None:
            if external_action.action_type == ActionType.RIICHI:
                best_tile = external_action.riichi_discard or external_action.tile
            elif external_action.action_type == ActionType.DISCARD:
                best_tile = external_action.tile

        candidates = list(available.can_discard)
        if not candidates and available.can_riichi:
            candidates = list(available.riichi_candidates)

        if not candidates:
            return None

        # Verify best_tile is legal in candidates
        matched_best = None
        if best_tile is not None:
            for t in candidates:
                if t.id == best_tile.id or (t.index34 == best_tile.index34 and t.is_red == best_tile.is_red):
                    matched_best = t
                    break
        if matched_best is None:
            ranked = self.greedy.rank_discards(game_view, candidates)
            matched_best = ranked[0] if ranked else candidates[0]
        best_tile = matched_best

        # Extract Mortal evaluation details if available
        mortal_evals: list[dict] = []
        if external_ai is not None and hasattr(external_ai, "get_last_evaluations"):
            mortal_evals = external_ai.get_last_evaluations()

        def _find_candidate_by_mortal_name(name: str, pool: list[Tile]) -> Optional[Tile]:
            for t in pool:
                if name == "5mr" and t.is_red and t.suit == 0:
                    return t
                if name == "5pr" and t.is_red and t.suit == 1:
                    return t
                if name == "5sr" and t.is_red and t.suit == 2:
                    return t
                if tile_to_mjai_str(t) == name or tile_to_simple_str(t) == name:
                    return t
            return None

        mortal_tile_map = {}
        for me in mortal_evals:
            t = _find_candidate_by_mortal_name(me["name"], candidates)
            if t is not None:
                key = (t.index34, t.is_red)
                if key not in mortal_tile_map:
                    mortal_tile_map[key] = me

        def _eval_tile(tile: Tile) -> DiscardCandidateEval:
            test_34 = list(hand_34)
            test_34[tile.index34] -= 1
            post_s = shanten(test_34)
            ukeire = _cached_ukeire(tuple(test_34), tuple(features.visible_34), post_s, is_sanma)
            win_prob = self.ev_model.estimate_win_probability(
                post_s, ukeire, game_view.remaining_tiles, game_view.is_dealer
            ) * 100.0
            deal_in_risk = self.ev_model.estimate_deal_in_probability(tile, features) * 100.0
            is_safe = bool(features.riichi_opponents and tile.index34 in features.common_genbutsu)
            is_dora = bool(tile.is_red or tile.index34 in features.dora_indices)
            note = ""
            if is_safe:
                note = "100% Genbutsu Safe"
            elif features.riichi_opponents and tile.index34 in features.suji_safe:
                note = "Suji Safe"
            elif is_dora:
                note = "Dora"

            q_val = None
            m_prob = None
            t_key = (tile.index34, tile.is_red)
            if t_key in mortal_tile_map:
                q_val = mortal_tile_map[t_key].get("q_value")
                m_prob = mortal_tile_map[t_key].get("prob_pct")

            return DiscardCandidateEval(
                tile=tile,
                shanten=post_s,
                ukeire=ukeire,
                win_prob=win_prob,
                deal_in_risk=deal_in_risk,
                is_safe=is_safe,
                is_dora=is_dora,
                note=note,
                q_value=q_val,
                mortal_prob=m_prob,
            )

        best_eval = _eval_tile(best_tile)

        # Ranked alternatives: prioritize Mortal's real Q-value rankings if available
        alternatives = []
        seen_34 = {best_tile.index34}
        if mortal_evals:
            for me in mortal_evals:
                t = _find_candidate_by_mortal_name(me["name"], candidates)
                if t is not None and t.index34 not in seen_34:
                    seen_34.add(t.index34)
                    alternatives.append(_eval_tile(t))
                    if len(alternatives) >= 3:
                        break

        # Fallback to greedy ranking if no mortal evals were found
        if not alternatives:
            other_candidates = []
            for t in candidates:
                if t.index34 not in seen_34:
                    seen_34.add(t.index34)
                    other_candidates.append(t)
            ranked_others = self.greedy.rank_discards(game_view, other_candidates)
            alternatives = [_eval_tile(t) for t in ranked_others[:2]]
        # Compute tactical defense posture (Push / Fold / Mawashi) and safe tiles radar
        tactical_stance = ""
        safe_tiles: List[Tuple[Tile, str]] = []
        if features.riichi_opponents:
            cur_s = shanten(hand_34)
            should_defend = self.greedy._should_defend(game_view, candidates)
            if cur_s == 0:
                tactical_stance = "push_tenpai"
            elif not should_defend:
                tactical_stance = "push_mawashi"
            else:
                tactical_stance = "fold"

            seen_safe_34 = set()
            for t in candidates:
                if t.index34 in seen_safe_34:
                    continue
                if t.index34 in features.common_genbutsu:
                    seen_safe_34.add(t.index34)
                    safe_tiles.append((t, "genbutsu"))
                elif t.index34 in features.suji_safe:
                    seen_safe_34.add(t.index34)
                    safe_tiles.append((t, "suji"))
                elif t.index34 in features.kabe_no_chance:
                    seen_safe_34.add(t.index34)
                    safe_tiles.append((t, "kabe"))
        elif game_view.remaining_tiles <= 25:
            tactical_stance = "late_defense"
        else:
            tactical_stance = "attack"

        return CoachSuggestion(
            best_tile=best_tile,
            best_eval=best_eval,
            alternatives=alternatives,
            action=external_action,
            engine_name=display_engine_name,
            gemini_advice=gemini_advice,
            tactical_stance=tactical_stance,
            safe_tiles=safe_tiles,
        )

    def review_decision(
        self,
        game_view: GameView,
        available: AvailableActions,
        chosen_action: Action,
        turn_number: int = 1,
    ) -> TurnReview:
        """Analyze and classify a player's decision."""
        if chosen_action.action_type not in (ActionType.DISCARD, ActionType.RIICHI):
            return TurnReview(
                turn_number=turn_number,
                player_idx=chosen_action.player,
                chosen_action=chosen_action,
                best_action=chosen_action,
                rating=MoveRating.BEST,
                score_loss_ev=0.0,
                ukeire_chosen=0,
                ukeire_best=0,
                shanten_chosen=0,
                shanten_best=0,
                deal_in_risk_chosen=0.0,
                deal_in_risk_best=0.0,
                explanation="Optimal special action.",
            )

        features = extract_public_features(game_view)
        hand_34 = game_view.my_hand.to_34_array()
        is_sanma = features.is_sanma

        chosen_tile = chosen_action.riichi_discard or chosen_action.tile or game_view.my_hand.closed_tiles[-1]
        ranked_tiles = self.greedy.rank_discards(game_view, available.can_discard)
        best_tile = ranked_tiles[0] if ranked_tiles else chosen_tile

        # Shape metrics for chosen
        c_test = list(hand_34)
        c_test[chosen_tile.index34] -= 1
        c_shanten = shanten(c_test)
        c_ukeire = _cached_ukeire(tuple(c_test), tuple(features.visible_34), c_shanten, is_sanma)
        c_risk = self.ev_model.estimate_deal_in_probability(chosen_tile, features)

        # Shape metrics for best
        b_test = list(hand_34)
        b_test[best_tile.index34] -= 1
        b_shanten = shanten(b_test)
        b_ukeire = _cached_ukeire(tuple(b_test), tuple(features.visible_34), b_shanten, is_sanma)
        b_risk = self.ev_model.estimate_deal_in_probability(best_tile, features)

        c_ev = self.ev_model.evaluate_discard_ev(game_view, chosen_tile, c_shanten, c_ukeire, 4000.0)
        b_ev = self.ev_model.evaluate_discard_ev(game_view, best_tile, b_shanten, b_ukeire, 4000.0)
        ev_loss = max(0.0, b_ev.expected_value - c_ev.expected_value)

        # Rating classification
        if chosen_tile.index34 == best_tile.index34:
            rating = MoveRating.BEST
            explanation = "Best discard found by AI."
        elif features.riichi_opponents and c_risk > 0.15 and b_risk <= 0.05:
            rating = MoveRating.BLUNDER
            explanation = f"Dangerous push into Riichi: {chosen_tile} has {c_risk*100:.1f}% deal-in risk."
        elif c_shanten > b_shanten:
            rating = MoveRating.BLUNDER
            explanation = f"Regressed shanten from {b_shanten} to {c_shanten}."
        elif ev_loss > 1200:
            rating = MoveRating.MISTAKE
            explanation = f"Lost {b_ukeire - c_ukeire} ukeire tiles (-{ev_loss:.0f} EV)."
        elif ev_loss > 400:
            rating = MoveRating.INACCURACY
            explanation = f"Slight shape inefficiency (-{ev_loss:.0f} EV)."
        else:
            rating = MoveRating.GOOD
            explanation = "Solid alternative move."

        alt_notes = [(str(t), self.ev_model.evaluate_discard_ev(game_view, t, b_shanten, 0, 4000.0).expected_value) for t in ranked_tiles[:3]]

        best_act = Action(ActionType.DISCARD, chosen_action.player, tile=best_tile)
        return TurnReview(
            turn_number=turn_number,
            player_idx=chosen_action.player,
            chosen_action=chosen_action,
            best_action=best_act,
            rating=rating,
            score_loss_ev=ev_loss,
            ukeire_chosen=c_ukeire,
            ukeire_best=b_ukeire,
            shanten_chosen=c_shanten,
            shanten_best=b_shanten,
            deal_in_risk_chosen=c_risk * 100,
            deal_in_risk_best=b_risk * 100,
            explanation=explanation,
            top_alternatives=alt_notes,
        )
