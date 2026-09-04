"""Public feature extraction for Mahjong AI players.

Extracts visible tile counts, dora indices, live tile counts, suji, kabe,
and tedashi/tsumogiri defense features strictly within the public information barrier.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from mahjong.core.tile import (
    ALL_TILES_136,
    RED_FIVE_MAN,
    RED_FIVE_PIN,
    RED_FIVE_SOU,
    Tile,
    YAOCHU_INDICES,
    next_tile_index,
)
from mahjong.player.base import GameView, OpponentView


@dataclass
class PublicFeatures:
    """Publicly observable board state features."""
    visible_34: List[int]                     # Total visible copies (0..4) of each tile 0..33
    live_34: List[int]                        # Remaining live unrevealed copies in wall / opponent hands
    dora_indices: Set[int]                    # 34-indices of active dora tiles
    is_sanma: bool
    
    # Defensive features
    riichi_opponents: List[OpponentView]
    genbutsu_by_seat: Dict[int, Set[int]]     # seat -> set of 100% genbutsu 34-indices
    common_genbutsu: Set[int]                 # genbutsu across all active riichi opponents
    suji_safe: Set[int]                       # regular suji safe tile indices
    tedashi_suji: Set[int]                    # backward compatibility
    early_suji: Set[int] = field(default_factory=set) # high-confidence early/tsumogiri suji
    trap_suji: Set[int] = field(default_factory=set)  # declaration or late tedashi suji (trap risk!)
    kabe_no_chance: Set[int] = field(default_factory=set) # 0% chance wall-blocked indices (all 4 visible)
    kabe_one_chance: Set[int] = field(default_factory=set) # 1-copy remaining wall indices
    kokushi_possible: bool = False


def extract_public_features(game_view: GameView) -> PublicFeatures:
    """Extract all public features from the current GameView."""
    is_sanma = len(game_view.opponents) == 2
    visible = [0] * 34

    # 1. Own hand (closed + melds + discards + kita)
    for tile in game_view.my_hand.closed_tiles:
        visible[tile.index34] += 1
    # Hand.draw() stores the drawn tile in closed_tiles as well as draw_tile.
    # Count it only when a legacy/imported Hand keeps it separate.
    if (game_view.my_hand.draw_tile and
            all(t.id != game_view.my_hand.draw_tile.id
                for t in game_view.my_hand.closed_tiles)):
        visible[game_view.my_hand.draw_tile.index34] += 1
    for tile in game_view.my_hand.discard_pool:
        visible[tile.index34] += 1
    for meld in game_view.my_hand.melds:
        for tile in meld.tiles:
            visible[tile.index34] += 1
    if is_sanma:
        visible[30] += game_view.my_kita_count

    # 2. Dora indicators
    for tile in game_view.dora_indicators:
        visible[tile.index34] += 1

    # 3. Opponents (melds + discards + kita)
    for opp in game_view.opponents:
        for tile in opp.discard_pool:
            visible[tile.index34] += 1
        for meld in opp.melds:
            for tile in meld.tiles:
                visible[tile.index34] += 1
        if is_sanma:
            visible[30] += opp.kita_count

    # Clamp visible counts to maximum 4
    for i in range(34):
        if is_sanma and 1 <= i <= 7:
            visible[i] = 4  # 2-8m are completely removed in sanma
        else:
            visible[i] = min(4, visible[i])

    # Live unrevealed copies remaining
    live = [max(0, 4 - visible[i]) for i in range(34)]
    if is_sanma:
        for i in range(1, 8):
            live[i] = 0

    # Dora 34-indices
    dora_indices = {
        next_tile_index(ind.index34, is_sanma)
        for ind in game_view.dora_indicators
    }

    # Defensive reading for Riichi opponents
    riichi_opponents = [opp for opp in game_view.opponents if opp.is_riichi]
    genbutsu_by_seat: Dict[int, Set[int]] = {}
    genbutsu_list = []
    
    for opp in riichi_opponents:
        g_set = {t.index34 for t in opp.discard_pool}
        genbutsu_by_seat[opp.seat] = g_set
        genbutsu_list.append(g_set)

    common_genbutsu = set.intersection(*genbutsu_list) if genbutsu_list else set()

    # Suji calculation
    suji_safe: Set[int] = set()
    tedashi_suji: Set[int] = set()
    early_suji: Set[int] = set()
    trap_suji: Set[int] = set()

    for opp in riichi_opponents:
        decl_idx = opp.riichi_discard_index
        for i, d in enumerate(opp.discard_pool):
            if d.index34 < 27:
                suit = d.index34 // 9
                num = (d.index34 % 9) + 1
                is_tsumogiri = (i < len(opp.discard_is_tsumogiri) and opp.discard_is_tsumogiri[i])
                is_pre_riichi = (decl_idx < 0 or i <= decl_idx)
                is_riichi_decl = (decl_idx >= 0 and i == decl_idx)
                # Tedashi within 2 turns of riichi or the declaration tile itself
                is_late_tedashi = (not is_tsumogiri and is_pre_riichi and decl_idx >= 0 and i >= decl_idx - 1)

                target_suji = set()
                if num == 4:
                    target_suji.add(suit * 9 + 0)  # 1
                    target_suji.add(suit * 9 + 6)  # 7
                elif num == 5:
                    target_suji.add(suit * 9 + 1)  # 2
                    target_suji.add(suit * 9 + 7)  # 8
                elif num == 6:
                    target_suji.add(suit * 9 + 2)  # 3
                    target_suji.add(suit * 9 + 8)  # 9

                suji_safe.update(target_suji)
                if not is_tsumogiri and is_pre_riichi:
                    tedashi_suji.update(target_suji)
                # Suji traps: riichi declaration tile and late hand discards
                if is_riichi_decl or is_late_tedashi:
                    trap_suji.update(target_suji)
                # High-confidence early or tsumogiri discards
                if i <= 5 or is_tsumogiri:
                    early_suji.update(target_suji)

    # Kabe (Wall blocks) calculation
    kabe_no_chance: Set[int] = set()
    kabe_one_chance: Set[int] = set()

    for suit in range(3):
        base = suit * 9
        # Terminal Kabe (blocking 1s and 9s)
        if visible[base + 1] >= 4:  # 2 is all visible -> 1 is No-Chance for ryanmen
            kabe_no_chance.add(base + 0)
        elif visible[base + 1] == 3:
            kabe_one_chance.add(base + 0)

        if visible[base + 7] >= 4:  # 8 is all visible -> 9 is No-Chance
            kabe_no_chance.add(base + 8)
        elif visible[base + 7] == 3:
            kabe_one_chance.add(base + 8)

        # 3 and 7 Kabe (blocking 1,2 and 8,9)
        if visible[base + 2] >= 4:
            kabe_no_chance.add(base + 0)
            kabe_no_chance.add(base + 1)
        if visible[base + 6] >= 4:
            kabe_no_chance.add(base + 7)
            kabe_no_chance.add(base + 8)

    # Kokushi Musou threat assessment
    kokushi_possible = False
    for opp in riichi_opponents:
        if len(opp.melds) == 0:
            terminals_in_pool = sum(1 for d in opp.discard_pool if d.index34 in YAOCHU_INDICES)
            if terminals_in_pool <= 2:
                kokushi_possible = True
                break

    return PublicFeatures(
        visible_34=visible,
        live_34=live,
        dora_indices=dora_indices,
        is_sanma=is_sanma,
        riichi_opponents=riichi_opponents,
        genbutsu_by_seat=genbutsu_by_seat,
        common_genbutsu=common_genbutsu,
        suji_safe=suji_safe,
        tedashi_suji=tedashi_suji,
        early_suji=early_suji,
        trap_suji=trap_suji,
        kabe_no_chance=kabe_no_chance,
        kabe_one_chance=kabe_one_chance,
        kokushi_possible=kokushi_possible,
    )
