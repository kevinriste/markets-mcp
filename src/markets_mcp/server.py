"""markets-mcp: read-only prediction-market odds (Polymarket + Kalshi) for an LLM, with page links, volume and history.

Serves Streamable HTTP at /mcp (internal docker network only, no auth: the data is public and keyless) or stdio.
"""
import argparse
import asyncio
import functools
import json
from typing import Literal

from mcp.server.fastmcp import FastMCP

from markets_mcp import kalshi, nfl, polymarket
from markets_mcp.util import Http

INSTRUCTIONS = """Live, read-only odds from two prediction markets: Polymarket and Kalshi.
- `prob` is the market's implied chance (0-1) of the outcome. Prices move; say when you read them if it matters.
- Every event carries `url`, the venue's own public page. Give the user that link whenever you cite a market.
- Volume units differ: Kalshi counts contracts traded (each pays $1), Polymarket counts USD. Don't add them together.
- Drill down with the ids a result gives: `event_id` -> get_event, `market_id` -> price_history.
- NFL first: nfl_games (this week's games, both venues side by side: moneyline, main spread, main total) and
  nfl_futures (champion, AFC, NFC, playoffs). Anything else (elections, economics, other sports): search_markets."""

http = Http()
mcp = FastMCP("markets", instructions=INSTRUCTIONS)
Venue = Literal["polymarket", "kalshi"]
Window = Literal["1d", "1w", "1m", "all"]


def compact(fn):
    """Tools answer with one compact JSON text block (no pretty-print, no duplicate structured copy): about half the
    tokens, and Calliope saves anything over its inline limit as a file anyway."""
    @functools.wraps(fn)
    async def run(*a, **kw) -> str:
        return json.dumps(await fn(*a, **kw), separators=(",", ":"), ensure_ascii=False)
    run.__annotations__ = {**fn.__annotations__, "return": str}
    return run


BRIEF = ("label", "prob", "outcomes", "volume", "volume_usd", "change_24h", "market_id", "result")


def _brief(ev: dict) -> dict:
    return ev | {"markets": [{k: m[k] for k in BRIEF if k in m} for m in ev.get("markets") or []]}


def _trim(ev: dict, n: int, sort: str = "prob") -> dict:
    ms = ev.get("markets") or []
    keyf = (lambda m: -(m.get("prob") or 0)) if sort == "prob" else (lambda m: -(m.get("volume") or m.get("volume_usd") or 0))
    ms = sorted(ms, key=keyf)
    out = ev | {"markets": ms[:n]}
    if len(ms) > n:
        out["more_markets"] = len(ms) - n
    return out


@mcp.tool(title='Search markets')
@compact
async def search_markets(query: str, venue: Literal["both", "polymarket", "kalshi"] = "both", limit: int = 6,
                         include_closed: bool = False) -> dict:
    """Find prediction markets by topic on Polymarket and/or Kalshi (e.g. "2028 Democratic nominee", "Fed rate cut
    December", "Chiefs Super Bowl"). Returns events per venue, biggest volume first, each with its page `url`,
    `event_id`, volume and its leading markets (`label`, `prob`, `market_id`). Use get_event for every market of one
    event, price_history for a market's trend."""
    limit = max(1, min(limit, 15))
    calls = {}
    if venue in ("both", "polymarket"):
        calls["polymarket"] = polymarket.search(http, query, limit, include_closed)
    if venue in ("both", "kalshi"):
        calls["kalshi"] = kalshi.search(http, query, limit, include_closed)
    results = await asyncio.gather(*calls.values(), return_exceptions=True)
    out: dict = {"query": query}
    for name, res in zip(calls, results):
        if isinstance(res, Exception):
            out[name] = {"error": f"{type(res).__name__}: {res}"}
            continue
        vkey = "volume_usd" if name == "polymarket" else "volume"
        res = sorted(res, key=lambda e: -(e.get(vkey) or 0))[:limit]
        out[name] = [_brief(_trim(e, 6)) for e in res]
    return out


@mcp.tool(title='Market event')
@compact
async def get_event(venue: Venue, event_id: str, market_filter: str | None = None, max_markets: int = 30,
                    sort: Literal["prob", "volume"] = "prob", include_closed: bool = False) -> dict:
    """One event with its markets: `prob`, bid/ask, last trade, 24h change, volume, 24h volume, open interest or
    liquidity, close time, and the page `url`. `event_id` is the Polymarket slug or the Kalshi event ticker from
    another tool's result. `market_filter` keeps markets whose label (or Polymarket market type, e.g. "spreads",
    "totals", "moneyline") contains the text: big game events list hundreds of props."""
    if venue == "polymarket":
        ev = await polymarket.get_event(http, event_id, include_closed)
    else:
        ev = await kalshi.get_event(http, event_id)
    if ev is None:
        return {"error": f"no {venue} event {event_id!r}"}
    if market_filter:
        f = market_filter.lower()
        ev["markets"] = [m for m in ev["markets"] if f in (m.get("label") or "").lower() or f == (m.get("type") or "")]
    return _trim(ev, max(1, min(max_markets, 100)), sort)


@mcp.tool(title='Price history')
@compact
async def price_history(venue: Venue, market_id: str, window: Window = "1w", outcome: str | None = None) -> dict:
    """How one market's probability moved: up to ~40 points over the window plus a summary (start, end, change,
    high, low). Kalshi points also carry the contracts traded per bucket and the window's total volume; Polymarket
    history is price only, with volume totals (24h/1w/1m). `market_id` comes from another tool's result. For a
    Polymarket market with named outcomes (a game's moneyline), `outcome` picks the side (default: the first)."""
    if venue == "polymarket":
        return await polymarket.price_history(http, market_id, window, outcome)
    return await kalshi.price_history(http, market_id, window)


@mcp.tool(title='NFL games')
@compact
async def nfl_games(team: str | None = None, days: int = 8) -> dict:
    """Upcoming NFL games on both venues side by side: moneyline (each team's win chance), the main spread and the
    main total (the traded line priced nearest 50%), volume, both page `url`s, and ids for drill-down (get_event for
    the full spread/total ladders and props, price_history for a trend). `team` accepts a name, city, nickname or
    abbreviation; `days` looks ahead (max 21)."""
    return await nfl.games(http, team, max(1, min(days, 21)))


@mcp.tool(title='NFL futures')
@compact
async def nfl_futures(kind: Literal["champion", "afc", "nfc", "playoffs"] = "champion", team: str | None = None) -> dict:
    """NFL season futures on both venues side by side, one row per team, likeliest first: Super Bowl champion,
    AFC or NFC champion, or making the playoffs. Each row has each venue's `prob`, volume, 24h change and
    `market_id` (for price_history); each venue's event `url` is in the header."""
    return await nfl.futures(http, kind, team)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="markets_mcp")
    p.add_argument("--http", action="store_true", help="serve Streamable HTTP at /mcp (default: stdio)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    a = p.parse_args(argv)
    if not a.http:
        mcp.run()
        return
    mcp.settings.host, mcp.settings.port = a.host, a.port
    mcp.settings.stateless_http = True
    mcp.settings.json_response = True
    # reached by container name on an internal network (Host: markets-mcp:8000), never published
    mcp.settings.transport_security.enable_dns_rebinding_protection = False
    mcp.run(transport="streamable-http")
