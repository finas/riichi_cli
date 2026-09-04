"""Explainable discard advisor for the human helper panel.

The advisor is intentionally read-only.  It evaluates legal discards using
information available in ``GameView`` and never mutates the live hand.
"""

from copy import copy
from dataclasses import dataclass
from typing import List

from mahjong.core.tile import Tile, ALL_TILES_136, next_tile_index
from mahjong.player.base import GameView
from mahjong.rules.shanten import shanten
from mahjong.rules.agari import get_waiting_tiles


@dataclass(frozen=True)
class DiscardAdvice:
    tile: Tile
    shanten: int
    ukeire: int
    dora: int
    danger: int
    shape: int
    waits: tuple[int, ...] = ()
    accepts: tuple[int, ...] = ()
    is_dora: bool = False

@dataclass(frozen=True)
class BlockDecomposition:
    total_blocks: int
    completed_sets: int
    protoruns: int
    pairs: tuple[int, ...]
    status_key: str  # "helper.blocks_fixed", "helper.blocks_surplus", "helper.blocks_sparse"


def analyze_5_blocks(counts_34: list[int], melds_count: int = 0) -> BlockDecomposition:
    c = list(counts_34)
    pairs = [i for i in range(34) if c[i] >= 2]
    sets = melds_count
    protoruns = 0

    # 1. Extract triplets
    for i in range(34):
        if c[i] >= 3:
            sets += 1
            c[i] -= 3

    # 2. Extract completed sequences
    for s in (0, 9, 18):
        for i in range(s, s + 7):
            while c[i] > 0 and c[i+1] > 0 and c[i+2] > 0:
                sets += 1
                c[i] -= 1
                c[i+1] -= 1
                c[i+2] -= 1

    # 3. Extract protoruns (adjacent and one-gap)
    for s in (0, 9, 18):
        for i in range(s, s + 8):
            while c[i] > 0 and c[i+1] > 0:
                protoruns += 1
                c[i] -= 1
                c[i+1] -= 1
        for i in range(s, s + 7):
            while c[i] > 0 and c[i+2] > 0:
                protoruns += 1
                c[i] -= 1
                c[i+2] -= 1

    rem_pairs = sum(1 for i in range(34) if c[i] >= 2)
    pair_block = rem_pairs if rem_pairs > 0 else (1 if len(pairs) > 0 else 0)
    total_blocks = sets + protoruns + pair_block

    if total_blocks == 5:
        status_key = "helper.blocks_fixed"
    elif total_blocks >= 6:
        status_key = "helper.blocks_surplus"
    else:
        status_key = "helper.blocks_sparse"

    return BlockDecomposition(
        total_blocks=total_blocks,
        completed_sets=sets,
        protoruns=protoruns,
        pairs=tuple(pairs),
        status_key=status_key,
    )


def potential_yaku_names(game_view: GameView, advice: DiscardAdvice) -> tuple[str, ...]:
    """Identify tactical yaku plans and 5-block structure for the hand."""
    counts = game_view.my_hand.to_34_array()
    counts[advice.tile.index34] -= 1
    total_tiles = sum(counts)
    if total_tiles <= 0:
        return ()

    open_melds = getattr(game_view.my_hand, "melds", [])
    open_melds_count = len(open_melds)
    is_menzen = bool(getattr(game_view.my_hand, "is_menzen", True))

    blocks = analyze_5_blocks(counts, open_melds_count)
    names: list[str] = []

    pairs = [i for i in range(34) if counts[i] >= 2]
    pair_count = len(pairs)
    triplets = [i for i in range(34) if counts[i] >= 3]

    my_wind_idx = getattr(getattr(game_view, "my_wind", None), "index34", 27)
    round_wind_idx = getattr(getattr(game_view, "round_wind", None), "index34", 27)
    valuable_honors = {31, 32, 33, my_wind_idx, round_wind_idx}
    has_yakuhai_pair = any(counts[h] >= 2 for h in valuable_honors)
    has_yakuhai_triplet = any(counts[h] >= 3 for h in valuable_honors)

    honors_count = sum(counts[27:34])
    suit_counts = [sum(counts[s:s + 9]) for s in (0, 9, 18)]
    max_suit = max(suit_counts)

    yaochu_count = sum(counts[i] for i in (0, 8, 9, 17, 18, 26, *range(27, 34)))
    simples_count = total_tiles - yaochu_count

    dora_indices = _dora_indices(game_view)
    dora_count = sum(counts[i] for i in dora_indices)
    dora_count += sum(1 for t in game_view.my_hand.closed_tiles
                      if t.is_red and t.index34 != advice.tile.index34)

    # 1. Chiitoitsu (Seven Pairs)
    if is_menzen and pair_count >= 4:
        names.append("helper.plan_chiitoi")

    # 2. Flush routes
    if max_suit >= 9 and honors_count == 0:
        names.append("helper.plan_chinitsu")
    elif (max_suit + honors_count) >= 9 and max_suit >= 6:
        names.append("helper.plan_honitsu")

    # 3. Yakuhai speed attack
    if has_yakuhai_triplet or has_yakuhai_pair:
        names.append("helper.plan_open_yakuhai")

    # 4. Tanyao (All Simples)
    if yaochu_count == 0:
        names.append("helper.plan_tanyao")
    elif yaochu_count <= 2 and simples_count >= 10:
        names.append("helper.plan_tanyao")

    # 5. Sanshoku (Mixed Triple Sequence)
    for start in range(1, 8):
        m_start = start - 1
        p_start = 9 + start - 1
        s_start = 18 + start - 1
        m_fit = sum(counts[m_start + k] > 0 for k in range(3))
        p_fit = sum(counts[p_start + k] > 0 for k in range(3))
        s_fit = sum(counts[s_start + k] > 0 for k in range(3))
        if m_fit >= 2 and p_fit >= 2 and s_fit >= 2 and (m_fit + p_fit + s_fit) >= 7:
            names.append("helper.plan_sanshoku")
            break

    # 6. Ittsu (Pure Straight)
    for s_base in (0, 9, 18):
        c1 = sum(counts[s_base + k] > 0 for k in range(3))
        c2 = sum(counts[s_base + 3 + k] > 0 for k in range(3))
        c3 = sum(counts[s_base + 6 + k] > 0 for k in range(3))
        if c1 >= 2 and c2 >= 2 and c3 >= 2 and (c1 + c2 + c3) >= 7:
            names.append("helper.plan_ittsu")
            break

    # 7. Toitoi (All Triplets)
    if (len(triplets) + pair_count) >= 4 and not is_menzen:
        names.append("helper.plan_toitoi")

    # 8. Riichi Pinfu / Dora / Menzen line
    if is_menzen:
        is_pinfu_candidate = (
            len(triplets) == 0 and
            not any(counts[h] >= 2 for h in valuable_honors) and
            honors_count <= 2 and
            blocks.completed_sets + blocks.protoruns >= 3
        )
        if is_pinfu_candidate and "helper.plan_chiitoi" not in names and "helper.plan_chinitsu" not in names:
            names.insert(0, "helper.plan_pinfu")
        elif dora_count >= 2:
            names.insert(0, "helper.plan_dora_value")
        elif not names:
            names.append("helper.plan_riichi")

    if not names:
        names.append("helper.plan_call_cautious" if not is_menzen else "helper.plan_menzen")

    # 9. 5-Block Status
    names.append(blocks.status_key)

    return tuple(names)

def _visible_counts(game_view: GameView) -> List[int]:
    counts = [0] * 34
    is_sanma = len(game_view.opponents) == 2
    hand = game_view.my_hand
    for tile in hand.closed_tiles:
        counts[tile.index34] += 1
    for meld in hand.melds:
        for tile in meld.tiles:
            counts[tile.index34] += 1
    for tile in hand.discard_pool:
        counts[tile.index34] += 1
    if is_sanma:
        counts[30] += game_view.my_kita_count
    for opp in game_view.opponents:
        for tile in opp.discard_pool:
            counts[tile.index34] += 1
        for meld in opp.melds:
            for tile in meld.tiles:
                counts[tile.index34] += 1
        if is_sanma:
            counts[30] += opp.kita_count
    for tile in game_view.dora_indicators:
        counts[tile.index34] += 1
    if is_sanma:
        for idx in range(1, 8):
            counts[idx] = 4
    return counts


def _dora_indices(game_view: GameView) -> set[int]:
    sanma = len(game_view.opponents) == 2
    return {next_tile_index(tile.index34, sanma)
            for tile in game_view.dora_indicators}


def _danger(game_view: GameView, tile: Tile, visible: List[int]) -> int:
    riichi = [opp for opp in game_view.opponents if opp.is_riichi]
    if not riichi:
        return 0
    index = tile.index34
    # Genbutsu is the safest discard against every riichi player.
    if all(any(t.index34 == index for t in opp.discard_pool) for opp in riichi):
        return 0
    if visible[index] >= 3:
        return 1
    if tile.is_honor:
        return 2 if visible[index] >= 2 else 5
    if tile.is_terminal:
        return 3
    return 7


def _shape_value(game_view: GameView, tile: Tile) -> int:
    """Estimate how expensive it is to remove a tile from the hand shape."""
    counts = game_view.my_hand.to_34_array()
    idx = tile.index34
    value = 0
    if counts[idx] >= 2:
        value += 8  # Pair/triplet is a strong structural resource.
    if tile.is_honor:
        if idx in (game_view.my_wind.index34, game_view.round_wind.index34):
            value += 7  # Potential double yakuhai.
        elif counts[idx] == 1:
            # A lone non-value honor is normally the first tile to release;
            # do not make it look more useful than an isolated number tile.
            value += 0
        return value
    number = idx % 9
    # Connected neighbors make a tile part of a useful sequence shape.
    for delta in (-2, -1, 1, 2):
        neighbor = idx + delta
        if 0 <= neighbor < 27 and neighbor // 9 == idx // 9:
            if counts[neighbor] > 0:
                value += 2 if abs(delta) == 1 else 1
    # Isolated terminals are the cheapest tiles to release.
    return value


def advise_discards(game_view: GameView, legal_discards: List[Tile],
                    limit: int = 3) -> List[DiscardAdvice]:
    """Rank legal discards from strongest to weakest.

    Ranking is lexicographic: shanten first, then exact ukeire, then hand
    value, then defense.  This keeps the advisor predictable while still
    avoiding the common mistake of sacrificing a red five for no gain.
    """
    if not legal_discards:
        return []
    current = game_view.my_hand.to_34_array()
    visible = _visible_counts(game_view)
    dora = _dora_indices(game_view)
    candidates: List[DiscardAdvice] = []
    seen = set()
    for tile in legal_discards:
        key = (tile.index34, tile.is_red)
        if key in seen:
            continue
        seen.add(key)
        after = list(current)
        after[tile.index34] -= 1
        if after[tile.index34] < 0:
            continue
        after_shanten = shanten(after)

        ukeire = 0
        accepts = []
        for draw_idx in range(34):
            if len(game_view.opponents) == 2 and 1 <= draw_idx <= 7:
                continue
            if visible[draw_idx] >= 4:
                continue
            probe = list(after)
            probe[draw_idx] += 1
            if shanten(probe) < after_shanten:
                ukeire += 4 - visible[draw_idx]
                accepts.append(draw_idx)

        dora_value = sum(after[i] for i in dora)
        dora_value += sum(1 for t in legal_discards
                          if t.is_red and t.index34 != tile.index34)
        # Discarding a red five is a deliberate value loss.
        if tile.is_red:
            dora_value -= 2
        waits = tuple(get_waiting_tiles(after)) if after_shanten == 0 else ()
        candidates.append(DiscardAdvice(
            tile=tile, shanten=after_shanten, ukeire=ukeire,
            dora=dora_value, danger=_danger(game_view, tile, visible),
            shape=_shape_value(game_view, tile),
            waits=waits,
            accepts=tuple(accepts),
            is_dora=tile.index34 in dora,
        ))

    candidates.sort(key=lambda a: (
        a.shanten, -a.ukeire, a.is_dora,
        0 if (a.tile.is_honor and a.shape == 0) else 1,
        a.tile.is_red, -a.dora,
        a.shape, a.danger,
        a.tile.index34, a.tile.id,
    ))
    return candidates[:max(1, limit)]


def recommend_route(game_view: GameView, advice: DiscardAdvice,
                    max_groups: int = 4) -> list[tuple[tuple[int, ...], Tile]]:
    """Return a compact two-step route after the recommended discard.

    Accepted draws are grouped by the best follow-up discard.  This is not a
    full tree search, but it gives the player a useful plan (``discard A ->
    draw one of B/C -> discard D``) without pretending to predict the wall.
    """
    groups: dict[tuple[int, bool], list[int]] = {}
    followups: dict[tuple[int, bool], Tile] = {}
    for draw_idx in advice.accepts:
        view = copy(game_view)
        view.my_hand = game_view.my_hand.clone()
        view.my_hand.closed_tiles.append(ALL_TILES_136[draw_idx * 4])
        options = advise_discards(view, list(view.my_hand.closed_tiles), limit=1)
        if not options:
            continue
        tile = options[0].tile
        key = (tile.index34, tile.is_red)
        groups.setdefault(key, []).append(draw_idx)
        followups[key] = tile
    ranked = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    return [(tuple(draws), followups[key]) for key, draws in ranked[:max_groups]]
