"""Polymarket (read-only, keyless): gamma for events/markets/search, CLOB for price history.

Volumes are USD (USDC) notional. A market's probability is its Yes price (outcomePrices, Polymarket's displayed
midpoint); a multi-outcome market (a game's moneyline: "49ers"/"Cowboys") lists every outcome with its price.
"""
import re
import time

from markets_mcp.util import Http, drop_none, iso, jlist, num, prob, summarize, downsample, vol

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


def event_url(slug: str) -> str:
    return f"https://polymarket.com/event/{slug}"


def market(m: dict) -> dict:
    outcomes, prices = jlist(m.get("outcomes")), [prob(p) for p in jlist(m.get("outcomePrices"))]
    # a game's spread/total rungs: the question names the side ("Spread: 49ers (-3.5)"), the group title does not
    label = (m.get("question") if m.get("sportsMarketType") else None) or m.get("groupItemTitle") or m.get("question") or m.get("slug")
    out = {"label": label, "market_id": str(m.get("id")), "volume_usd": vol(m.get("volumeNum") or m.get("volume")),
           "volume_24h_usd": vol(m.get("volume24hr")), "bid": prob(m.get("bestBid")), "ask": prob(m.get("bestAsk")),
           "last": prob(m.get("lastTradePrice")), "change_24h": prob(m.get("oneDayPriceChange")),
           "change_1w": prob(m.get("oneWeekPriceChange")), "change_1m": prob(m.get("oneMonthPriceChange")), "liquidity_usd": vol(m.get("liquidityNum")),
           "type": m.get("sportsMarketType"), "line": num(m.get("line")), "closes": (m.get("endDate") or "")[:16] or None,
           "closed": True if m.get("closed") else None}
    if [o.lower() for o in outcomes] == ["yes", "no"] and prices:
        out["prob"] = prices[0]
    else:
        out["outcomes"] = [{"name": o, "prob": p} for o, p in zip(outcomes, prices)]
        if prices:
            out["prob"] = prices[0]   # first outcome's price (the ladder helpers rank on it)
    return drop_none(out)


def event(e: dict, include_closed: bool = False) -> dict:
    ms = [market(m) for m in e.get("markets") or []]
    if not include_closed and any(not m.get("closed") for m in ms):
        ms = [m for m in ms if not m.get("closed")]
    return {"venue": "polymarket", "event_id": e.get("slug"), "title": e.get("title"), "url": event_url(e.get("slug", "")),
            "volume_usd": vol(e.get("volume")), "volume_24h_usd": vol(e.get("volume24hr")),
            "volume_1w_usd": vol(e.get("volume1wk")), "open_interest_usd": vol(e.get("openInterest")),
            "closes": (e.get("endDate") or "")[:16] or None, "closed": bool(e.get("closed")), "markets": ms}


async def search(http: Http, query: str, limit: int, include_closed: bool) -> list[dict]:
    params = {"q": query, "limit_per_type": limit, "search_tags": "false", "search_profiles": "false"}
    if not include_closed:
        params["events_status"] = "active"
    d = await http.get(f"{GAMMA}/public-search", params)
    return [event(e, include_closed) for e in (d.get("events") or [])]


async def get_event(http: Http, slug: str, include_closed: bool = False) -> dict | None:
    d = await http.get(f"{GAMMA}/events", {"slug": slug})
    return event(d[0], include_closed) if d else None


async def list_events(http: Http, tag: str, limit: int = 100, **extra) -> list[dict]:
    """Raw gamma events for a tag (open only), newest-closing first unless `order` says otherwise."""
    params = {"tag_slug": tag, "active": "true", "closed": "false", "limit": limit, **extra}
    return await http.get(f"{GAMMA}/events", params)


# window -> (CLOB interval, fidelity in minutes)
WINDOWS = {"1d": ("1d", 60), "1w": ("1w", 360), "1m": ("1m", 1440), "all": ("max", 1440)}


async def price_history(http: Http, market_id: str, window: str, outcome: str | None) -> dict:
    m = await http.get(f"{GAMMA}/markets/{market_id}")
    outcomes, tokens = jlist(m.get("outcomes")), jlist(m.get("clobTokenIds"))
    if not tokens:
        return {"error": f"market {market_id} has no order book (no price history)"}
    i = 0
    if outcome:
        names = [o.lower() for o in outcomes]
        if outcome.lower() not in names:
            return {"error": f"outcome {outcome!r} not in {outcomes}"}
        i = names.index(outcome.lower())
    interval, fidelity = WINDOWS[window]
    h = await http.get(f"{CLOB}/prices-history", {"market": tokens[i], "interval": interval, "fidelity": fidelity})
    series = [(iso(p["t"]), float(p["p"])) for p in h.get("history") or [] if p.get("p") is not None]
    return drop_none({
        "venue": "polymarket", "market_id": str(market_id), "label": m.get("groupItemTitle") or m.get("question"),
        "outcome": outcomes[i] if outcomes else None, "window": window, "summary": summarize(series),
        "points": [[t, round(p, 3)] for t, p in downsample(series, 40)],
        "volume_usd": vol(m.get("volumeNum")), "volume_24h_usd": vol(m.get("volume24hr")),
        "volume_1w_usd": vol(m.get("volume1wk")), "volume_1m_usd": vol(m.get("volume1mo")),
        "note": "Polymarket's history is price only; volume is given as totals over the window.",
        "url": event_url((m.get("events") or [{}])[0].get("slug") or m.get("slug", "")),
    })


GAME_SLUG = re.compile(r"^nfl-([a-z]+)-([a-z]+)-(\d{4}-\d{2}-\d{2})$")


async def nfl_game_events(http: Http, days: int) -> list[dict]:
    """Open Polymarket NFL game events closing within `days` (raw gamma events; their slugs are nfl-<away>-<home>-<date>)."""
    now = time.time()
    lo, hi = iso(now - 6 * 3600).replace("Z", ":00Z"), iso(now + days * 86400).replace("Z", ":00Z")
    raw = await list_events(http, "nfl", 100, end_date_min=lo, end_date_max=hi, order="endDate", ascending="true")
    return [e for e in raw if GAME_SLUG.match(e.get("slug", ""))]
