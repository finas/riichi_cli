"""Google Gemini AI Coach using the agy CLI (Antigravity / Vertex AI).

Provides strategic Riichi Mahjong advice, yaku planning, and discard suggestions
powered by Google Gemini models (e.g. gemini-3.8-flash-low).
"""

import os
import re
import shutil
import subprocess
import time
from typing import List, Optional, Tuple

from mahjong.core.tile import Tile, ALL_TILES_136
from mahjong.core.player_state import Wind
from mahjong.engine.action import Action, ActionType, AvailableActions
from mahjong.player.base import Player, GameView
from mahjong.player.greedy_ai import GreedyAI
from mahjong.player.advisor import advise_discards
from mahjong.rules.shanten import shanten
from mahjong.ui.tile_display import tile_to_simple_str, TILE_SHORT_NAMES

# In-memory advice cache: keyed by turn signature to avoid duplicate network calls
_ADVICE_CACHE: dict[Tuple, str] = {}


def format_gemini_advice_markup(text: str) -> str:
    """Format LLM Markdown output into styled Rich console markup."""
    if not text:
        return ""
    lines = []
    for line in text.splitlines():
        # Headers: ### Title -> [bold magenta]Title[/bold magenta]
        line = re.sub(r"^#{1,6}\s*(.+)$", r"[bold magenta]\1[/bold magenta]", line)
        # Bold: **text** -> [bold cyan]\1[/bold cyan]
        line = re.sub(r"\*\*([^\*]+)\*\*", r"[bold cyan]\1[/bold cyan]", line)
        # Italic: *text* -> [italic]\1[/italic]
        line = re.sub(r"(?<!\*)\*([^\*]+)\*(?!\*)", r"[italic]\1[/italic]", line)
        # Inline code: `text` -> [yellow]\1[/yellow]
        line = re.sub(r"`([^`]+)`", r"[yellow]\1[/yellow]", line)
        # Unordered bullet lists: * item or - item ->   • item
        line = re.sub(r"^\s*[\*\-]\s+(.+)$", r"  • \1", line)
        # Numbered lists: 1. item ->   [bold yellow]\1[/bold yellow] item
        line = re.sub(r"^\s*(\d+\.)\s+(.+)$", r"  [bold yellow]\1[/bold yellow] \2", line)
        lines.append(line)
    return "\n".join(lines)
def _format_open_meld(m: Meld, lang: str = "zh") -> str:
    """Format a single open meld with its tile faces and call type."""
    from mahjong.core.meld import MeldType
    tiles_str = " ".join(tile_to_simple_str(t) for t in m.tiles)
    type_names_zh = {
        MeldType.CHI: "吃",
        MeldType.PON: "碰",
        MeldType.DAIMINKAN: "大明杠",
        MeldType.ANKAN: "暗杠",
        MeldType.SHOUMINKAN: "加杠",
    }
    type_names_en = {
        MeldType.CHI: "Chi",
        MeldType.PON: "Pon",
        MeldType.DAIMINKAN: "Daiminkan",
        MeldType.ANKAN: "Ankan",
        MeldType.SHOUMINKAN: "Shouminkan",
    }
    name = (type_names_zh if lang == "zh" else type_names_en).get(m.meld_type, str(m.meld_type.value))
    red_tag = " (含赤宝牌)" if lang == "zh" and m.contains_red() else (" (red dora)" if m.contains_red() else "")
    return f"{name}[{tiles_str}]{red_tag}"


def get_agy_path() -> Optional[str]:
    """Locate the agy CLI binary."""
    custom_path = os.environ.get("MAHJONG_AGY_PATH", "").strip()
    if custom_path:
        if os.path.isfile(custom_path) and os.access(custom_path, os.X_OK):
            return custom_path
        return None
    return shutil.which("agy")


def is_gemini_available() -> bool:
    """Check if agy binary is installed and executable."""
    return get_agy_path() is not None


def get_gemini_model() -> str:
    """Return configured Gemini model name."""
    return os.environ.get("MAHJONG_GEMINI_MODEL", "gemini-3.8-flash-low").strip()


def build_gemini_prompt(
    game_view: GameView,
    available: AvailableActions,
    language: Optional[str] = None,
) -> str:
    """Construct a structured game state prompt for Gemini, defaulting to Chinese."""
    lang = (language or os.environ.get("MAHJONG_GEMINI_LANG", "zh")).lower()
    hand = game_view.my_hand
    draw_tile = hand.draw_tile
    closed = [t for t in hand.closed_tiles if t != draw_tile]
    closed.sort()

    closed_str = " ".join(tile_to_simple_str(t) for t in closed)
    draw_str = tile_to_simple_str(draw_tile) if draw_tile else "None"
    melds_str = ", ".join(str(m) for m in hand.melds) if hand.melds else ("无 (门清)" if lang == "zh" else "None (Menzen)")

    dora_str = " ".join(tile_to_simple_str(t) for t in game_view.dora_indicators)
    cur_shanten = shanten(hand.to_34_array())

    # Legal discard candidates with basic ukeire
    candidates_info = []
    if available.can_discard:
        advice = advise_discards(game_view, available.can_discard, limit=5)
        for adv in advice:
            sh_label = "向听" if lang == "zh" else "shanten"
            uk_label = "进张" if lang == "zh" else "ukeire"
            candidates_info.append(
                f"{tile_to_simple_str(adv.tile)} ({sh_label}: {adv.shanten}, {uk_label}: {adv.ukeire})"
            )
    candidates_str = ", ".join(candidates_info) if candidates_info else ("无" if lang == "zh" else "None")

    # Riichi threats
    riichi_opps = [
        f"{opp.seat}号位 ({opp.name})" if lang == "zh" else f"Seat {opp.seat} ({opp.name})"
        for opp in game_view.opponents
        if opp.is_riichi
    ]

    # Opponent detailed status: all open melds and recent discards
    opp_lines = []
    for opp in game_view.opponents:
        seat_lbl = f"{opp.seat}号位 ({opp.name})" if lang == "zh" else f"Seat {opp.seat} ({opp.name})"
        riichi_tag = (" [已立直]" if opp.is_riichi else "") if lang == "zh" else (" [Riichi]" if opp.is_riichi else "")
        dealer_tag = (" [庄家]" if opp.is_dealer else "") if lang == "zh" else (" [Dealer]" if opp.is_dealer else "")
        kita_tag = (f" (拔北 x{opp.kita_count})" if opp.kita_count > 0 else "") if lang == "zh" else (f" (Kita x{opp.kita_count})" if opp.kita_count > 0 else "")

        if opp.melds:
            melds_detail = ", ".join(_format_open_meld(m, lang) for m in opp.melds)
            meld_part = f"副露明牌: {melds_detail}" if lang == "zh" else f"Open melds: {melds_detail}"
        else:
            meld_part = "门前清 (无副露)" if lang == "zh" else "Closed (Menzen)"

        if opp.discard_pool:
            pool_str = " ".join(tile_to_simple_str(t) for t in opp.discard_pool[-6:])
            discards_part = f" | 舍牌河近况: {pool_str}" if lang == "zh" else f" | Recent discards: {pool_str}"
        else:
            discards_part = ""
        opp_lines.append(f"  * {seat_lbl}{dealer_tag}{riichi_tag}{kita_tag}: {meld_part}{discards_part}")
    opp_status_str = "\n".join(opp_lines) if opp_lines else ("  * 无对手信息" if lang == "zh" else "  * No opponent info")

    # Check if this is a call decision point (Chi, Pon, Kan)
    has_call_decision = bool(
        available.can_chi
        or available.can_pon
        or available.can_daiminkan
        or available.can_ankan
        or available.can_shouminkan
    )
    # Calculate defense features and safe tiles radar
    from mahjong.player.ai_features import extract_public_features
    features = extract_public_features(game_view)

    genbutsu_tiles = []
    suji_tiles = []
    kabe_tiles = []
    seen_safe = set()
    all_my_tiles = closed + ([draw_tile] if draw_tile else [])
    for t in all_my_tiles:
        if t.index34 in seen_safe:
            continue
        if t.index34 in features.common_genbutsu:
            seen_safe.add(t.index34)
            genbutsu_tiles.append(tile_to_simple_str(t))
        elif t.index34 in features.suji_safe:
            seen_safe.add(t.index34)
            suji_tiles.append(tile_to_simple_str(t))
        elif t.index34 in features.kabe_no_chance:
            seen_safe.add(t.index34)
            kabe_tiles.append(tile_to_simple_str(t))

    has_defense_context = bool(features.riichi_opponents or game_view.remaining_tiles <= 25)
    greedy_local = GreedyAI("TempGreedy")
    local_should_defend = greedy_local._should_defend(game_view, available.can_discard) if available.can_discard else False

    if cur_shanten == 0:
        local_posture = "已听牌，好形或高打点时支持对攻 (Push)" if lang == "zh" else "Tenpai: push supported"
    elif local_should_defend:
        local_posture = "向听数较远且胜率低，本地攻守引擎强烈建议【彻底弃和】(Fold/Betaori)" if lang == "zh" else "Fold (Betaori) recommended"
    elif features.riichi_opponents:
        local_posture = "1向听或好形打点充足，可考虑【攻防兼备/兜牌】(Mawashi)" if lang == "zh" else "Mawashi: balance offense & defense"
    else:
        local_posture = "局势平稳，建议积极【进攻做牌】(Attack)" if lang == "zh" else "Attack / Build speed"
    if lang == "zh":
        wind_zh = {
            Wind.EAST: "东风 (Ton)",
            Wind.SOUTH: "南风 (Nan)",
            Wind.WEST: "西风 (Sha)",
            Wind.NORTH: "北风 (Pei)",
        }
        round_name = wind_zh.get(game_view.round_wind, "东风")
        seat_name = wind_zh.get(game_view.my_wind, "东风")
        shanten_label = "听牌 (Tenpai)" if cur_shanten == 0 else f"{cur_shanten}向听"
        dealer_str = "是 (庄家)" if game_view.is_dealer else "否 (子家)"

        actions = []
        if available.can_tsumo: actions.append("自摸 (Tsumo)")
        if available.can_ron: actions.append("荣和 (Ron)")
        if available.can_riichi: actions.append("立直 (Riichi)")
        if available.can_pon: actions.append("碰 (Pon)")
        if available.can_chi: actions.append("吃 (Chi)")
        if (available.can_ankan or available.can_shouminkan or available.can_daiminkan): actions.append("杠 (Kan)")
        if available.can_kita: actions.append("拔北 (Kita)")
        if available.can_kyuushu: actions.append("九种九牌")
        if available.can_discard: actions.append("打牌 (Discard)")
        actions_str = ", ".join(actions)

        threats_str = ", ".join(riichi_opps) if riichi_opps else "无对手立直"

        call_details = ""
        if has_call_decision:
            call_lines = []
            if game_view.last_discard:
                from_str = f"{game_view.last_discard_player}号位" if game_view.last_discard_player is not None else "对手"
                call_lines.append(f"- 对手打出的牌: {tile_to_simple_str(game_view.last_discard)} (由 {from_str} 打出)")
            if available.can_chi:
                chi_opts = [f"吃 {' '.join(tile_to_simple_str(t) for t in m.tiles)}" for m in available.can_chi]
                call_lines.append(f"- 可选吃牌: {', '.join(chi_opts)}")
            if available.can_pon:
                pon_opts = [f"碰 {' '.join(tile_to_simple_str(t) for t in m.tiles)}" for m in available.can_pon]
                call_lines.append(f"- 可选碰牌: {', '.join(pon_opts)}")
            if available.can_daiminkan:
                kan_opts = [f"大明杠 {' '.join(tile_to_simple_str(t) for t in m.tiles)}" for m in available.can_daiminkan]
                call_lines.append(f"- 可选大明杠: {', '.join(kan_opts)}")
            if available.can_ankan:
                ankan_opts = [f"暗杠 {' '.join(tile_to_simple_str(t) for t in t_list)}" for t_list in available.can_ankan]
                call_lines.append(f"- 可选暗杠: {', '.join(ankan_opts)}")
            if available.can_shouminkan:
                skan_opts = [f"加杠 {tile_to_simple_str(t)}" for t in available.can_shouminkan]
                call_lines.append(f"- 可选加杠: {', '.join(skan_opts)}")
            call_lines.append("- 亦可选择: 跳过/不鸣保持门前清 (Skip)")
            call_details = "\n【当前鸣牌（副露）决策点】:\n" + "\n".join(call_lines) + "\n"

        defense_details = ""
        if has_defense_context:
            def_lines = []
            if features.riichi_opponents:
                def_lines.append(f"- 危险警报: {threats_str} 已立直！")
            if genbutsu_tiles:
                def_lines.append(f"- 手牌对立直绝对安全牌 (现物 100%安全): {', '.join(genbutsu_tiles)}")
            else:
                def_lines.append("- 手牌对立直绝对安全牌 (现物 100%安全): 无现物，需跟打或打筋牌")
            if suji_tiles or kabe_tiles:
                semi = []
                if suji_tiles: semi.append(f"筋牌: {', '.join(suji_tiles)}")
                if kabe_tiles: semi.append(f"壁牌: {', '.join(kabe_tiles)}")
                def_lines.append(f"- 手牌半安全牌: {', '.join(semi)}")
            def_lines.append(f"- 本地攻守引擎研判: {local_posture}")
            defense_details = "\n【立直威胁与防守安全牌态势】:\n" + "\n".join(def_lines) + "\n"

        if has_call_decision:
            guidance_ask = (
                "请务必使用中文严格按以下3点输出针对当前鸣牌选项的指导 (每点1-2句话):\n"
                "1. 建议操作: (明确给出【吃】/【碰】/【杠】还是【跳过/保持门清】，若鸣牌指明具体组合)\n"
                "2. 目标役种与做牌构想: (分析鸣牌后役种是否确立，如役牌、断幺，对比门清打点潜力)\n"
                "3. 战术理由与大局观: (权衡鸣牌换取速度 vs 失去立直、防守能力缩减的利弊)"
            )
        elif has_defense_context:
            guidance_ask = (
                "请务必使用中文严格按以下3点输出指导 (结合攻守判断与防守策略, 每点1-2句话):\n"
                "1. 建议操作 / 舍牌: (明确写出建议切哪张牌，标明是进攻牌效还是防守安牌)\n"
                "2. 攻守判断 (Push / Fold): (明确给出【全攻】、【兜牌】还是【彻底弃和】，解析对手威胁与手牌差距)\n"
                "3. 防守点评与做牌大局观: (点评手牌中的现物安牌与切忌打出的危险生张，若兜牌/进攻说明后续规划)"
            )
        else:
            guidance_ask = (
                "请务必使用中文严格按以下3点输出简洁指导 (每点1-2句话):\n"
                "1. 建议操作 / 舍牌: (明确写出建议打出的牌，如“打 西”或“立直 打 西”或“自摸”)\n"
                "2. 目标役种与做牌构想: (如 平和、断幺、立直、役牌、混一色等路线及预期打点)\n"
                "3. 战术理由与大局观: (分析牌效进张、五区块取舍、进攻攻守判断或安牌防守考虑)"
            )
        prompt = f"""你是一位专业的日本立直麻将大师级教练，正在指导 0号位（玩家）。
请使用中文提供精炼、专业且有说服力的大局观与战术建议。

对局状态:
- 场风与自风: 场风 {round_name}, 自风 {seat_name}, 是否庄家: {dealer_str}
- 点数: {game_view.my_score}, 牌山剩余: {game_view.remaining_tiles}, 本场数: {game_view.honba}
- 宝牌指示牌: {dora_str}
- 你的手牌: {closed_str} | 摸牌: {draw_str}
- 副露: {melds_str}
- 向听数: {shanten_label}
- 推荐候选切牌: {candidates_str}
- 当前可用操作: {actions_str}
- 对手立直威胁: {threats_str}
- 对手副露明牌与舍牌河态势:
{opp_status_str}
{call_details}{defense_details}
{guidance_ask}"""
        return prompt
    # Default English prompt
    wind_names = {
        Wind.EAST: "East (Ton)",
        Wind.SOUTH: "South (Nan)",
        Wind.WEST: "West (Sha)",
        Wind.NORTH: "North (Pei)",
    }
    round_name = wind_names.get(game_view.round_wind, "East")
    seat_name = wind_names.get(game_view.my_wind, "East")
    shanten_label = "Tenpai" if cur_shanten == 0 else f"{cur_shanten}-shanten"

    actions = []
    if available.can_tsumo: actions.append("Tsumo (Win)")
    if available.can_ron: actions.append("Ron (Win)")
    if available.can_riichi: actions.append("Riichi")
    if available.can_pon: actions.append("Pon")
    if available.can_chi: actions.append("Chi")
    if (available.can_ankan or available.can_shouminkan or available.can_daiminkan): actions.append("Kan")
    if available.can_kita: actions.append("Kita")
    if available.can_kyuushu: actions.append("Kyuushu (9 Terminal/Honor redraw)")
    if available.can_discard: actions.append("Discard")
    actions_str = ", ".join(actions)

    threats_str = ", ".join(riichi_opps) if riichi_opps else "None"

    prompt = f"""You are a professional Japanese Riichi Mahjong coach advising Seat 0.
Game State:
- Round: {round_name}, Seat: {seat_name}, Dealer: {"Yes" if game_view.is_dealer else "No"}
- Score: {game_view.my_score}, Remaining tiles in wall: {game_view.remaining_tiles}, Honba: {game_view.honba}
- Dora indicators: {dora_str}
- Your Hand: {closed_str} | Drew: {draw_str}
- Melds: {melds_str}
- Hand status: {shanten_label}
- Top legal candidate discards: {candidates_str}
- Available actions: {actions_str}
- Riichi threats from opponents: {threats_str}
- Opponent open melds and discard rivers:
{opp_status_str}
Provide concise advice in 3 parts:
1. Recommended Action/Discard: (e.g. Discard 西, Riichi, or Tsumo)
2. Target Yaku & Hand Plan: (e.g. Riichi, Pinfu, Tanyao, Yakuhai)
3. Tactical Reasoning: (1-2 sentences on tile efficiency, 5-block shape, or safety)"""
    return prompt

def query_gemini(
    prompt: str,
    model: Optional[str] = None,
    timeout: float = 25.0,
) -> Optional[str]:
    """Execute non-interactive prompt via agy CLI."""
    agy_path = get_agy_path()
    if not agy_path:
        return None

    active_model = model or get_gemini_model()
    cmd = [
        agy_path,
        "-p",
        prompt,
        "--model",
        active_model,
        "--disable-slash-commands",
    ]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except (subprocess.TimeoutExpired, OSError, subprocess.SubprocessError):
        return None
    return None


def extract_recommended_tile(response_text: str, candidates: List[Tile]) -> Optional[Tile]:
    """Parse the recommended tile from Gemini's response text."""
    if not candidates:
        return None

    # 1. Look for explicit "Discard X" patterns
    discard_patterns = [
        r"(?:discard|play|drop|打出|打|切|舍弃|舍)\s*[:\*]*\s*([0-9][mps]|东|南|西|北|白|发|發|中|East|South|West|North|White|Green|Red\s*Dragon)",
        r"(?:discard|play|drop|打出|打|切|舍弃|舍)\s*[:\*]*\s*\*\*([^\*]+)\*\*",
        r"\*\*discard\s+([^\*]+)\*\*",
        r"\*\*(?:打出|打|切|舍弃|舍)\s*([^\*]+)\*\*",
    ]
    for pat in discard_patterns:
        m = re.search(pat, response_text, re.IGNORECASE)
        if m:
            matched_str = m.group(1).strip()
            for c in candidates:
                s = tile_to_simple_str(c)
                n = TILE_SHORT_NAMES[c.index34]
                if s.lower() in matched_str.lower() or n.lower() in matched_str.lower():
                    return c
                honor_map = {
                    27: ["east", "ton", "东", "東"],
                    28: ["south", "nan", "南"],
                    29: ["west", "sha", "西"],
                    30: ["north", "pei", "北"],
                    31: ["white", "haku", "白"],
                    32: ["green", "hatsu", "发", "發"],
                    33: ["red", "chun", "中", "红中", "紅中"],
                }
                if c.index34 in honor_map and any(h in matched_str.lower() for h in honor_map[c.index34]):
                    return c

    # 2. Look for candidate tiles in Section 1
    sec1_match = re.search(
        r"(?:1\.|\bRecommended Action/Discard\b|\bRecommended Discard\b|建议操作\s*/?\s*舍牌|建议舍牌|建议打出|建议操作)(.*?)(?:2\.|\n\n|\Z)",
        response_text,
        re.IGNORECASE | re.DOTALL,
    )
    sec1_text = sec1_match.group(1) if sec1_match else response_text

    matched_candidates = []
    for c in candidates:
        s = tile_to_simple_str(c)
        n = TILE_SHORT_NAMES[c.index34]
        if re.search(rf"\b{re.escape(s)}\b", sec1_text, re.IGNORECASE) or re.search(
            rf"\b{re.escape(n)}\b", sec1_text, re.IGNORECASE
        ):
            pos = sec1_text.lower().find(s.lower())
            if pos == -1:
                pos = sec1_text.lower().find(n.lower())
            matched_candidates.append((pos, c))
            continue
        honor_map = {
            27: ["east", "ton", "东", "東"],
            28: ["south", "nan", "南"],
            29: ["west", "sha", "西"],
            30: ["north", "pei", "北"],
            31: ["white", "haku", "白"],
            32: ["green", "hatsu", "发", "發"],
            33: ["red dragon", "chun", "中", "红中", "紅中"],
        }
        if c.index34 in honor_map:
            for h in honor_map[c.index34]:
                pattern = rf"\b{re.escape(h)}\b" if h.isascii() else re.escape(h)
                if re.search(pattern, sec1_text, re.IGNORECASE):
                    pos = sec1_text.lower().find(h.lower())
                    matched_candidates.append((pos, c))
                    break

    if matched_candidates:
        matched_candidates.sort(key=lambda x: x[0])
        return matched_candidates[0][1]

    return None


def extract_recommended_action(
    response_text: str,
    available: AvailableActions,
    game_view: GameView,
) -> Action:
    """Derive an engine Action from Gemini response, with safe native fallback."""
    player_idx = available.player

    # Check for immediate win commands
    if available.can_tsumo and re.search(r"自摸|\btsumo\b", response_text, re.IGNORECASE):
        return Action(ActionType.TSUMO, player_idx)
    if available.can_ron and re.search(r"荣和|榮和|点炮|\bron\b", response_text, re.IGNORECASE):
        return Action(ActionType.RON, player_idx)

    # Check for Riichi declaration
    wants_riichi = available.can_riichi and bool(re.search(r"立直|\briichi\b", response_text, re.IGNORECASE))
    # Check for call decisions (Chi, Pon, Kan, Skip) when not a normal discard turn
    if not available.can_discard:
        if re.search(r"跳过|不鸣|不吃|不碰|不杠|\bskip\b|\bpass\b", response_text, re.IGNORECASE):
            return Action(ActionType.SKIP, player_idx)
        if available.can_pon and re.search(r"碰|\bpon\b", response_text, re.IGNORECASE):
            return Action(ActionType.PON, player_idx, meld=available.can_pon[0])
        if available.can_chi and re.search(r"吃|\bchi\b", response_text, re.IGNORECASE):
            return Action(ActionType.CHI, player_idx, meld=available.can_chi[0])
        if available.can_daiminkan and re.search(r"大明杠|杠|\bkan\b", response_text, re.IGNORECASE):
            return Action(ActionType.DAIMINKAN, player_idx, meld=available.can_daiminkan[0])
        if available.can_ankan and re.search(r"暗杠|杠|\bkan\b", response_text, re.IGNORECASE):
            return Action(ActionType.ANKAN, player_idx, tile=available.can_ankan[0][0])
        if available.can_shouminkan and re.search(r"加杠|小明杠|杠|\bkan\b", response_text, re.IGNORECASE):
            return Action(ActionType.SHOUMINKAN, player_idx, tile=available.can_shouminkan[0])
        return Action(ActionType.SKIP, player_idx)

    candidates = list(available.riichi_candidates) if wants_riichi else list(available.can_discard)
    if not candidates and available.can_discard:
        candidates = list(available.can_discard)

    tile = extract_recommended_tile(response_text, candidates)
    if tile is None and candidates:
        # Fallback to greedy AI top discard
        greedy = GreedyAI()
        ranked = greedy.rank_discards(game_view, candidates)
        tile = ranked[0] if ranked else candidates[0]

    if wants_riichi and tile is not None:
        return Action(ActionType.RIICHI, player_idx, tile=tile, riichi_discard=tile)

    if tile is not None:
        return Action(ActionType.DISCARD, player_idx, tile=tile)

    return Action(ActionType.SKIP, player_idx)


def _cache_key(game_view: GameView, available: AvailableActions) -> Tuple:
    """Generate cache key for current turn state."""
    hand = game_view.my_hand
    closed_tuple = tuple(sorted(t.id for t in hand.closed_tiles))
    draw_id = hand.draw_tile.id if hand.draw_tile else -1
    return (
        game_view.my_seat,
        game_view.remaining_tiles,
        closed_tuple,
        draw_id,
        bool(available.can_tsumo),
        bool(available.can_ron),
        bool(available.can_riichi),
        bool(available.can_pon),
        bool(available.can_chi),
        bool(available.can_ankan or available.can_shouminkan or available.can_daiminkan),
    )


def get_gemini_advice(
    game_view: GameView,
    available: AvailableActions,
    model: Optional[str] = None,
    timeout: float = 25.0,
) -> str:
    """Get Gemini strategic advice with caching and graceful offline fallback."""
    key = _cache_key(game_view, available)
    if key in _ADVICE_CACHE:
        return _ADVICE_CACHE[key]

    if not is_gemini_available():
        fallback_msg = (
            "[yellow]Google Gemini CLI (agy) is not found in PATH.[/yellow]\n"
            "Ensure [bold]agy[/bold] is installed and configured with credentials."
        )
        _ADVICE_CACHE[key] = fallback_msg
        return fallback_msg

    prompt = build_gemini_prompt(game_view, available)
    response = query_gemini(prompt, model=model, timeout=timeout)
    if not response:
        fallback_msg = (
            "[yellow]Could not retrieve advice from Google Gemini.[/yellow]\n"
            "Request timed out or network connection issue. Falling back to local heuristics."
        )
        return fallback_msg

    _ADVICE_CACHE[key] = response
    return response


class GeminiCoachPlayer(Player):
    """Coach player backed by Google Gemini via agy CLI, with safe GreedyAI fallback.

    By default, `auto_query` is False so that routine turns run instantly via local
    heuristics (0ms latency). In-depth Gemini LLM analysis is fetched on-demand
    when the player presses the 'G' key.
    """

    def __init__(
        self,
        name: str = "Google Gemini",
        model: Optional[str] = None,
        auto_query: bool = False,
    ):
        super().__init__(name)
        self.model = model or get_gemini_model()
        self.fallback = GreedyAI(f"{name}Fallback")
        self.auto_query = auto_query or (os.environ.get("MAHJONG_GEMINI_AUTO", "0") == "1")
        self.last_advice: Optional[str] = None
        self.last_action_source = "fallback"

    def choose_action(self, game_view: GameView, available: AvailableActions) -> Action:
        """Choose an action. Uses fast local fallback by default; queries Gemini if auto_query is enabled."""
        # Immediate wins do not require remote query
        if available.can_tsumo:
            self.last_action_source = "gemini"
            return Action(ActionType.TSUMO, available.player)
        if available.can_ron:
            self.last_action_source = "gemini"
            return Action(ActionType.RON, available.player)

        # In on-demand mode (default), normal turn actions use the instant local AI.
        # Deep LLM analysis is fetched on-demand when the player presses 'G'.
        if not self.auto_query:
            self.last_action_source = "fallback"
            return self.fallback.choose_action(game_view, available)

        if not is_gemini_available():
            self.last_action_source = "fallback"
            return self.fallback.choose_action(game_view, available)

        prompt = build_gemini_prompt(game_view, available)
        from rich.console import Console
        from mahjong.ui.i18n import t
        console = Console()
        with console.status(f"[bold cyan]{t('coach.consulting_gemini')}[/bold cyan]", spinner="dots"):
            response = query_gemini(prompt, model=self.model, timeout=25.0)

        if response:
            self.last_advice = response
            self.last_action_source = "external"
            action = extract_recommended_action(response, available, game_view)
            return action

        self.last_action_source = "fallback"
        return self.fallback.choose_action(game_view, available)

    def get_last_action_source(self) -> str:
        return self.last_action_source

    def get_last_advice(self) -> Optional[str]:
        return self.last_advice

    def close(self) -> None:
        """Clean up player resources."""
        if hasattr(self.fallback, "close"):
            self.fallback.close()
