"""Pure shaping: no network. Inputs are trimmed copies of real venue payloads (2026-11-12)."""
import datetime as dt

from markets_mcp import kalshi, nfl, polymarket
from markets_mcp.util import bucket, closest_to_even, downsample, summarize


def test_team_key_names_every_way():
    assert nfl.team_key("49ers") == "SF"
    assert nfl.team_key("San Francisco") == "SF"
    assert nfl.team_key("the Niners") == "SF"
    assert nfl.team_key("jax") == "JAC"          # Polymarket's abbreviation, Kalshi says JAC
    assert nfl.team_key("la") == "LAR"
    assert nfl.team_key("Los Angeles C") == "LAC"  # Kalshi's label
    assert nfl.team_key("New York G") == "NYG"
    assert nfl.team_key("Arizona Cardinals") == "ARI"
    assert nfl.team_key("New York") is None       # two teams: ambiguous
    assert nfl.team_key("Los Angeles") is None
    assert nfl.team_key("Toronto") is None


def test_game_keys_join_across_venues():
    k = nfl.kalshi_game_key("KXNFLGAME-26NOV12WASNYG", ["KXNFLGAME-26NOV12WASNYG-NYG", "KXNFLGAME-26NOV12WASNYG-WAS"])
    p = nfl.poly_game_key("nfl-was-nyg-2026-11-13")   # Thursday night: Polymarket dates it by the UTC day
    assert k == (dt.date(2026, 11, 12), frozenset({"WAS", "NYG"}))
    assert nfl.same_game(k, p)
    assert not nfl.same_game(k, nfl.poly_game_key("nfl-was-nyg-2026-12-20"))
    assert nfl.poly_game_key("nfl-was-nyg-2026-11-13-player-props") is None
    assert nfl.same_game(nfl.kalshi_game_key("KXNFLGAME-26NOV15JACTEN", ["X-JAC", "X-TEN"]),
                         nfl.poly_game_key("nfl-jax-ten-2026-11-15"))


def test_kalshi_prob_is_mid_when_tight_else_last():
    assert kalshi.implied("0.6500", "0.6700", "0.6700") == 0.66
    assert kalshi.implied("0.1000", "0.4000", "0.3000") == 0.3     # wide book: the last trade
    assert kalshi.implied("0.0000", "0.0000", "0.0000") is None    # nothing traded, no book


def test_kalshi_market_and_link():
    m = kalshi.market({"ticker": "KXSB-27-SF", "yes_sub_title": "San Francisco", "yes_bid_dollars": "0.1400",
                       "yes_ask_dollars": "0.1500", "last_price_dollars": "0.1500", "previous_price_dollars": "0.1600",
                       "volume_fp": "14250556.00", "volume_24h_fp": "83553.00", "status": "active"})
    assert m == {"label": "San Francisco", "market_id": "KXSB-27-SF", "prob": 0.145, "bid": 0.14, "ask": 0.15,
                 "last": 0.15, "change_24h": -0.01, "volume": 14250556, "volume_24h": 83553}
    assert kalshi.event_url("KXNFLGAME", "KXNFLGAME-26NOV15SFDAL", "San Francisco vs Dallas") == \
        "https://kalshi.com/markets/kxnflgame/san-francisco-vs-dallas/kxnflgame-26nov15sfdal"


def test_polymarket_yes_no_and_named_outcomes():
    yes_no = polymarket.market({"id": 559661, "groupItemTitle": "Jon Ossoff", "outcomes": '["Yes", "No"]',
                                "outcomePrices": '["0.2635", "0.7365"]', "volumeNum": 14990739.2})
    assert yes_no["prob"] == 0.264 and yes_no["label"] == "Jon Ossoff" and "outcomes" not in yes_no
    ml = polymarket.market({"id": 6248157, "question": "49ers vs. Cowboys", "groupItemTitle": "49ers vs. Cowboys",
                            "sportsMarketType": "spreads", "outcomes": '["49ers", "Cowboys"]',
                            "outcomePrices": '["0.655", "0.345"]'})
    assert ml["outcomes"] == [{"name": "49ers", "prob": 0.655}, {"name": "Cowboys", "prob": 0.345}]
    assert polymarket.event_url("nfl-sf-dal-2026-11-15") == "https://polymarket.com/event/nfl-sf-dal-2026-11-15"


def test_polymarket_event_hides_resolved_markets_unless_asked():
    e = {"slug": "s", "title": "t", "markets": [
        {"id": 1, "groupItemTitle": "A", "outcomes": '["Yes","No"]', "outcomePrices": '["1","0"]', "closed": True},
        {"id": 2, "groupItemTitle": "B", "outcomes": '["Yes","No"]', "outcomePrices": '["0.4","0.6"]'}]}
    assert [m["label"] for m in polymarket.event(e)["markets"]] == ["B"]
    assert [m["label"] for m in polymarket.event(e, include_closed=True)["markets"]] == ["A", "B"]


def test_main_line_is_traded_rung_nearest_even():
    ladder = [{"label": "-1.5", "prob": 0.63, "volume": 0}, {"label": "-3.5", "prob": 0.515, "volume": 9768},
              {"label": "-4.5", "prob": 0.49, "volume": 0}, {"label": "-5.5", "prob": 0.455, "volume_usd": 226}]
    assert closest_to_even(ladder)["label"] == "-3.5"   # -4.5 is nearer 50% but never traded
    assert closest_to_even([]) is None


def test_bucket_keeps_all_volume_and_downsample_keeps_ends():
    rows = [(str(i), i / 10, 1.0) for i in range(10)]
    b = bucket(rows, 3)
    assert len(b) <= 3 and sum(v for *_, v in b) == 10 and b[-1][:2] == ("9", 0.9)
    pts = list(range(100))
    d = downsample(pts, 5)
    assert d[0] == 0 and d[-1] == 99 and len(d) == 5
    s = summarize([("a", 0.5), ("b", 0.7), ("c", 0.6)])
    assert s["change"] == 0.1 and s["high"] == 0.7 and s["low"] == 0.5
