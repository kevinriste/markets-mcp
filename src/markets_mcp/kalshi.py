"""Kalshi (read-only, keyless): the public trade API v2 for events/markets/candlesticks, the site's v1 search.

Volumes are contracts traded (each contract pays $1, so a contract's price is its cost in dollars). A market's
probability is the bid/ask midpoint when the book is tight (spread <= 10c), else the last trade, which is how
Kalshi's own page displays it.
"""
import time

from markets_mcp.util import Http, bucket, drop_none, iso, num, prob, slugify, summarize, vol

API = "https://api.elections.kalshi.com/trade-api/v2"
SEARCH = "https://api.elections.kalshi.com/v1/search/series"


def event_url(series: str, event_ticker: str, title: str = "") -> str:
    # the middle segment is cosmetic: kalshi.com redirects any slug to the canonical one
    return f"https://kalshi.com/markets/{series.lower()}/{slugify(title)}/{event_ticker.lower()}"


def implied(bid, ask, last) -> float | None:
    b, a, l = num(bid), num(ask), num(last)
    if b and a and 0 < a - b <= 0.10:
        return round((a + b) / 2, 3)
    if l:
        return round(l, 3)
    if b and a:
        return round((a + b) / 2, 3)
    return None


def market(m: dict) -> dict:
    bid, ask, last = m.get("yes_bid_dollars"), m.get("yes_ask_dollars"), m.get("last_price_dollars")
    prev = num(m.get("previous_price_dollars"))
    lastf = num(last)
    return drop_none({
        "label": m.get("yes_sub_title") or m.get("subtitle") or m.get("title"), "market_id": m.get("ticker"),
        "prob": implied(bid, ask, last), "bid": prob(bid), "ask": prob(ask), "last": prob(last),
        "change_24h": round(lastf - prev, 3) if lastf is not None and prev else None,
        "volume": vol(m.get("volume_fp")), "volume_24h": vol(m.get("volume_24h_fp")),
        "open_interest": vol(m.get("open_interest_fp")), "closes": (m.get("close_time") or "")[:16] or None,
        "status": m.get("status") if m.get("status") not in ("active", "open") else None,
        "result": m.get("result") or None,
    })


def event(e: dict, markets: list | None = None, with_rules: bool = False) -> dict:
    raw = markets if markets is not None else (e.get("markets") or [])
    ms = [market(m) for m in raw]
    out = {"venue": "kalshi", "event_id": e.get("event_ticker"), "series": e.get("series_ticker"),
           "title": e.get("title"), "subtitle": e.get("sub_title") or None,
           "url": event_url(e.get("series_ticker", ""), e.get("event_ticker", ""), e.get("title", "")),
           "volume": sum(m.get("volume") or 0 for m in ms), "volume_24h": sum(m.get("volume_24h") or 0 for m in ms),
           "open_interest": sum(m.get("open_interest") or 0 for m in ms), "markets": ms}
    if with_rules and raw:
        out["rules"] = (raw[0].get("rules_primary") or "")[:600] or None
    return drop_none(out) | {"markets": ms}


async def search(http: Http, query: str, limit: int, include_closed: bool) -> list[dict]:
    d = await http.get(SEARCH, {"query": query, "page_size": max(limit * 2, 10)})
    out = []
    for s in d.get("current_page") or []:
        if not include_closed and not s.get("active_market_count"):
            continue
        ms = [drop_none({"label": m.get("yes_subtitle") or m.get("title"), "market_id": m.get("ticker"),
                         "prob": implied(m.get("yes_bid_dollars"), m.get("yes_ask_dollars"), m.get("last_price_dollars")),
                         "result": m.get("result") or None})
              for m in s.get("markets") or []]
        out.append(drop_none({
            "venue": "kalshi", "event_id": s.get("event_ticker"), "series": s.get("series_ticker"),
            "title": s.get("event_title"), "subtitle": s.get("event_subtitle"),
            "url": event_url(s.get("series_ticker", ""), s.get("event_ticker", ""), s.get("event_title", "")),
            "volume": vol(s.get("total_volume")), "category": s.get("category"),
            "active_markets": s.get("active_market_count"), "markets": ms}) | {"markets": ms})
        if len(out) >= limit:
            break
    return out


async def get_event(http: Http, event_ticker: str) -> dict | None:
    d = await http.get(f"{API}/events/{event_ticker.upper()}", {"with_nested_markets": "true"})
    e = d.get("event")
    if not e:
        return None
    return event(e, e.get("markets") or d.get("markets") or [], with_rules=True)


async def open_events(http: Http, series: str, limit: int = 100) -> list[dict]:
    d = await http.get(f"{API}/events", {"series_ticker": series, "status": "open", "with_nested_markets": "true",
                                         "limit": limit})
    return d.get("events") or []


# window -> (seconds back, candle period in minutes, points kept)
WINDOWS = {"1d": (86400, 60, 24), "1w": (7 * 86400, 60, 42), "1m": (30 * 86400, 1440, 30), "all": (730 * 86400, 1440, 60)}


async def _series_of(http: Http, ticker: str) -> str:
    m = (await http.get(f"{API}/markets/{ticker}")).get("market") or {}
    e = (await http.get(f"{API}/events/{m.get('event_ticker')}")).get("event") or {}
    return e.get("series_ticker") or ticker.split("-")[0]


async def price_history(http: Http, ticker: str, window: str) -> dict:
    ticker = ticker.upper()
    back, period, keep = WINDOWS[window]
    now = int(time.time())
    params = {"start_ts": now - back, "end_ts": now, "period_interval": period}
    series = ticker.split("-")[0]
    try:
        d = await http.get(f"{API}/series/{series}/markets/{ticker}/candlesticks", params)
    except Exception:
        series = await _series_of(http, ticker)
        d = await http.get(f"{API}/series/{series}/markets/{ticker}/candlesticks", params)
    rows = []
    for c in d.get("candlesticks") or []:
        p = c.get("price") or {}
        close = num(p.get("close_dollars"))
        if close is None:   # no trade in the bucket: fall back to the book's midpoint
            close = implied((c.get("yes_bid") or {}).get("close_dollars"), (c.get("yes_ask") or {}).get("close_dollars"), None)
        if close is None:
            continue
        rows.append((iso(c.get("end_period_ts")), close, num(c.get("volume_fp")) or 0.0))
    total = sum(v for *_, v in rows)
    kept = bucket(rows, keep)
    m = (await http.get(f"{API}/markets/{ticker}")).get("market") or {}
    return drop_none({
        "venue": "kalshi", "market_id": ticker, "label": m.get("yes_sub_title"), "window": window,
        "summary": summarize([(t, p) for t, p, _ in rows]) | {"volume_in_window": vol(total)},
        "points": [[t, round(p, 3), vol(v)] for t, p, v in kept],
        "points_format": "[bucket end time, closing prob, contracts traded in the bucket]",
        "volume": vol(m.get("volume_fp")), "volume_24h": vol(m.get("volume_24h_fp")),
        "url": event_url(series, m.get("event_ticker", ""), m.get("title", "")) if m.get("event_ticker") else None,
    })
