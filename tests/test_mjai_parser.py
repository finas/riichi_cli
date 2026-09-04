from tests.tenhou_replay.mjai_parser import parse_mjai_content, MJAITracker
from tests.tenhou_replay.parser import EventType


SAMPLE_MJAI_GAME = """
{"type":"start_game","names":["Player0","Player1","Player2","Player3"]}
{"type":"start_kyoku","bakaze":"E","kyoku":1,"honba":0,"kyotaku":0,"oya":0,"dora_marker":"1m","tehais":[["1m","2m","3m","4m","5mr","6m","7m","8m","9m","1p","2p","3p","4p"],["2s","3s","4s","5s","6s","7s","8s","E","S","W","N","P","F"],["1s","2s","3s","4s","5s","6s","7s","8s","9s","1p","2p","3p","4p"],["1m","2m","3m","4m","5m","6m","7m","8m","9m","1p","2p","3p","4p"]]}
{"type":"tsumo","actor":0,"pai":"5p"}
{"type":"dahai","actor":0,"pai":"4p","tsumogiri":false}
{"type":"tsumo","actor":1,"pai":"C"}
{"type":"dahai","actor":1,"pai":"E","tsumogiri":false}
{"type":"reach","actor":0}
{"type":"reach_accepted","actor":0}
{"type":"tsumo","actor":2,"pai":"9p"}
{"type":"dahai","actor":2,"pai":"9p","tsumogiri":true}
{"type":"tsumo","actor":3,"pai":"9p"}
{"type":"dahai","actor":3,"pai":"9p","tsumogiri":true}
{"type":"tsumo","actor":0,"pai":"5p"}
{"type":"hora","actor":0,"target":0,"pai":"5p"}
{"type":"end_kyoku"}
{"type":"end_game"}
"""


def test_mjai_tracker():
    tracker = MJAITracker()
    # Test red five
    r5m = tracker.allocate_tile("5mr")
    assert r5m == 16
    
    # Test normal 5m
    n5m = tracker.allocate_tile("5m")
    assert n5m in (17, 18, 19)

    # Test honor
    east = tracker.allocate_tile("E")
    assert east // 4 == 27


def test_parse_mjai_content():
    rounds = parse_mjai_content(SAMPLE_MJAI_GAME)
    assert len(rounds) == 1
    
    rd = rounds[0]
    assert rd.round_number == 0
    assert rd.dealer == 0
    assert len(rd.hands[0]) == 13
    assert rd.hands[0][4] == 16  # 5mr (Red Five)
    
    assert len(rd.events) == 12
    assert rd.events[0].event_type == EventType.DRAW
    assert rd.events[0].player == 0
    assert rd.events[1].event_type == EventType.DISCARD
    assert rd.events[4].event_type == EventType.RIICHI_DECLARE
    assert rd.events[5].event_type == EventType.RIICHI_SCORE
    assert rd.events[11].event_type == EventType.AGARI
