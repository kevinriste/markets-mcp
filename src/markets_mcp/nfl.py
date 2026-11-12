"""NFL: the 32 teams (one key per team across both venues), game boards and futures side by side."""
import asyncio
import datetime as dt
import re

from markets_mcp import kalshi, polymarket
from markets_mcp.util import Http, closest_to_even, drop_none

# key (Kalshi's abbreviation), full name, Kalshi's label, extra aliases (Polymarket abbreviations, nicknames)
TEAMS = [
    ("ARI", "Arizona Cardinals", "Arizona", []), ("ATL", "Atlanta Falcons", "Atlanta", []),
    ("BAL", "Baltimore Ravens", "Baltimore", []), ("BUF", "Buffalo Bills", "Buffalo", []),
    ("CAR", "Carolina Panthers", "Carolina", []), ("CHI", "Chicago Bears", "Chicago", []),
    ("CIN", "Cincinnati Bengals", "Cincinnati", []), ("CLE", "Cleveland Browns", "Cleveland", []),
    ("DAL", "Dallas Cowboys", "Dallas", []), ("DEN", "Denver Broncos", "Denver", []),
    ("DET", "Detroit Lions", "Detroit", []), ("GB", "Green Bay Packers", "Green Bay", []),
    ("HOU", "Houston Texans", "Houston", []), ("IND", "Indianapolis Colts", "Indianapolis", []),
    ("JAC", "Jacksonville Jaguars", "Jacksonville", ["jax", "jags"]), ("KC", "Kansas City Chiefs", "Kansas City", []),
    ("LV", "Las Vegas Raiders", "Las Vegas", ["lvr"]), ("LAC", "Los Angeles Chargers", "Los Angeles C", []),
    ("LAR", "Los Angeles Rams", "Los Angeles R", ["la"]), ("MIA", "Miami Dolphins", "Miami", []),
    ("MIN", "Minnesota Vikings", "Minnesota", []), ("NE", "New England Patriots", "New England", ["pats"]),
    ("NO", "New Orleans Saints", "New Orleans", ["nola"]), ("NYG", "New York Giants", "New York G", []),
    ("NYJ", "New York Jets", "New York J", []), ("PHI", "Philadelphia Eagles", "Philadelphia", []),
    ("PIT", "Pittsburgh Steelers", "Pittsburgh", []), ("SF", "San Francisco 49ers", "San Francisco", ["niners"]),
    ("SEA", "Seattle Seahawks", "Seattle", []), ("TB", "Tampa Bay Buccaneers", "Tampa Bay", ["bucs"]),
    ("TEN", "Tennessee Titans", "Tennessee", []), ("WAS", "Washington Commanders", "Washington", ["wsh"]),
]
FULL = {k: full for k, full, _, _ in TEAMS}
_ALIAS: dict[str, str] = {}
for k, full, label, extra in TEAMS:
    nick = full.split()[-1]
    for a in [k, full, label, nick, *extra]:
        _ALIAS.setdefault(a.lower(), k)
for amb in ("los angeles", "new york"):   # two teams each: needs the nickname
    _ALIAS.pop(amb, None)


def team_key(text: str | None) -> str | None:
    """Any way a venue or a person names a team (abbreviation, city, nickname, full name) -> its key, else None."""
    if not text:
        return None
    t = text.strip().lower()
    if t in _ALIAS:
        return _ALIAS[t]
    for k, full, _, extra in TEAMS:   # "the Niners", "San Francisco 49ers ML"
        nick = full.split()[-1].lower()
        if re.search(rf"\b({re.escape(nick)}|{'|'.join(map(re.escape, extra)) or '$^'})\b", t):
            return k
    return None


_MON = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}


def kalshi_game_key(event_ticker: str, market_tickers: list[str]) -> tuple[dt.date, frozenset] | None:
    """KXNFLGAME-26NOV15SFDAL (+ markets ...-SF, ...-DAL) -> (2026-11-15, {SF, DAL})."""
    m = re.match(r"^[A-Z0-9]+-(\d\d)([A-Z]{3})(\d\d)", event_ticker)
    if not m or m.group(2) not in _MON:
        return None
    teams = frozenset(filter(None, (team_key(t.rsplit("-", 1)[-1]) for t in market_tickers)))
    if len(teams) != 2:
        return None
    return dt.date(2000 + int(m.group(1)), _MON[m.group(2)], int(m.group(3))), teams


def poly_game_key(slug: str) -> tuple[dt.date, frozenset] | None:
    m = polymarket.GAME_SLUG.match(slug)
    if not m:
        return None
    teams = frozenset(filter(None, (team_key(m.group(1)), team_key(m.group(2)))))
    return (dt.date.fromisoformat(m.group(3)), teams) if len(teams) == 2 else None


def same_game(a: tuple[dt.date, frozenset], b: tuple[dt.date, frozenset]) -> bool:
    # Polymarket dates a night game by its UTC day, Kalshi by the US day: allow one day apart
    return a[1] == b[1] and abs((a[0] - b[0]).days) <= 1


def _kalshi_suffix(event_ticker: str) -> str:
    return event_ticker.split("-", 1)[1] if "-" in event_ticker else event_ticker


def _pick(m: dict, keys: tuple) -> dict:
    return {k: m[k] for k in keys if m.get(k) is not None}


SIDE = ("team", "prob", "change_24h", "volume", "volume_24h", "market_id")   # a board row stays small: 15 games fit inline


def _line(ms: list[dict]) -> dict | None:
    best = closest_to_even(ms)
    return _pick(best, ("label", "prob", "outcomes", "volume", "volume_usd", "market_id")) if best else None


async def games(http: Http, team: str | None, days: int) -> dict:
    want = team_key(team) if team else None
    if team and not want:
        return {"error": f"unknown team {team!r}"}
    k_games, k_spreads, k_totals, p_games = await asyncio.gather(
        kalshi.open_events(http, "KXNFLGAME"), kalshi.open_events(http, "KXNFLSPREAD"),
        kalshi.open_events(http, "KXNFLTOTAL"), polymarket.nfl_game_events(http, days), return_exceptions=True)
    errors = {n: str(x) for n, x in zip(("kalshi_games", "kalshi_spreads", "kalshi_totals", "polymarket"),
                                       (k_games, k_spreads, k_totals, p_games)) if isinstance(x, Exception)}
    k_games, k_spreads, k_totals, p_games = ([] if isinstance(x, Exception) else x
                                             for x in (k_games, k_spreads, k_totals, p_games))
    by_suffix = lambda evs: {_kalshi_suffix(e["event_ticker"]): e for e in evs}
    spreads, totals = by_suffix(k_spreads), by_suffix(k_totals)
    horizon = dt.date.today() + dt.timedelta(days=days)
    rows: list[dict] = []

    for e in k_games:
        key = kalshi_game_key(e["event_ticker"], [m["ticker"] for m in e.get("markets") or []])
        if not key or key[0] > horizon or (want and want not in key[1]):
            continue
        ev = kalshi.event(e)
        suffix = _kalshi_suffix(e["event_ticker"])
        side = {"url": ev["url"], "event_id": ev["event_id"], "volume": ev.get("volume"),
                "moneyline": [_pick(m | {"team": FULL.get(team_key(m.get("label")) or "", m.get("label"))}, SIDE)
                              for m in ev["markets"]]}
        if suffix in spreads:
            sp = kalshi.event(spreads[suffix])
            side["spread"] = (_line(sp["markets"]) or {}) | {"event_id": sp["event_id"]}
        if suffix in totals:
            tt = kalshi.event(totals[suffix])
            side["total"] = (_line(tt["markets"]) or {}) | {"event_id": tt["event_id"]}
        rows.append({"key": key, "date": key[0].isoformat(), "kalshi": side})

    for e in p_games:
        key = poly_game_key(e["slug"])
        if not key or (want and want not in key[1]):
            continue
        ev = polymarket.event(e)
        ml = next((m for m in ev["markets"] if m.get("type") == "moneyline"), None)
        side = {"url": ev["url"], "event_id": ev["event_id"], "volume_usd": ev.get("volume_usd"),
                "volume_24h_usd": ev.get("volume_24h_usd")}
        if ml:
            side["moneyline"] = _pick(ml, ("outcomes", "change_24h", "volume_usd", "volume_24h_usd", "market_id"))
        for kind in ("spreads", "totals"):
            line = _line([m for m in ev["markets"] if m.get("type") == kind])
            if line:
                side[kind[:-1]] = line
        order = {t.get("ordering"): t.get("name") for t in e.get("teams") or []}
        kick = next((m.get("gameStartTime") for m in e.get("markets") or [] if m.get("gameStartTime")), None)
        match = next((r for r in rows if "polymarket" not in r and same_game(r["key"], key)), None)
        if match is None:
            match = {"key": key, "date": key[0].isoformat()}
            rows.append(match)
        match["polymarket"] = side
        if order.get("away") and order.get("home"):
            match["away"], match["home"] = order["away"], order["home"]
        if kick:
            match["kickoff_utc"] = kick.replace(" ", "T")[:16] + "Z"

    for r in rows:
        teams = sorted(FULL[k] for k in r.pop("key")[1])
        r.setdefault("matchup", f"{r['away']} at {r['home']}" if r.get("away") else " vs ".join(teams))
    rows.sort(key=lambda r: (r.get("kickoff_utc") or r["date"], r["matchup"]))
    if len(rows) > 6:   # a whole week: keep each row to what a board needs (a team filter or get_event gives the rest)
        for r in rows:
            if r.get("kickoff_utc"):
                r.pop("date", None)
            for side in (r.get("kalshi"), r.get("polymarket")):
                if not side:
                    continue
                for k in ("spread", "total"):
                    if side.get(k):
                        side[k] = _pick(side[k], ("label", "prob", "event_id"))
                ml = side.get("moneyline")
                if isinstance(ml, list):
                    side["moneyline"] = [_pick(m, ("team", "prob", "market_id")) for m in ml]
                elif ml:
                    side["moneyline"] = _pick(ml, ("outcomes", "market_id"))
                side.pop("volume_24h_usd", None)
    out = {"games": [{"matchup": r.pop("matchup"), **r} for r in rows],
           "units": "prob = implied chance 0-1; kalshi volume = contracts ($1 payout each); polymarket volume = USD"}
    if errors:
        out["errors"] = errors
    return out


FUTURES = {  # kind -> (Kalshi series, Polymarket NFL-tag event title pattern)
    "champion": ("KXSB", r"^Pro Football: \d{4} Champion\b"),
    "afc": ("KXNFLAFCCHAMP", r"\bAFC Champion\b"),
    "nfc": ("KXNFLNFCCHAMP", r"\bNFC Champion\b"),
    "playoffs": ("KXNFLPLAYOFF", r"Team to Make (the )?Postseason"),
}


async def futures(http: Http, kind: str, team: str | None) -> dict:
    if kind not in FUTURES:
        return {"error": f"kind must be one of {sorted(FUTURES)}"}
    want = team_key(team) if team else None
    if team and not want:
        return {"error": f"unknown team {team!r}"}
    series, pattern = FUTURES[kind]
    k_evs, p_evs = await asyncio.gather(kalshi.open_events(http, series),
                                        polymarket.list_events(http, "nfl", 100, order="volume", ascending="false"),
                                        return_exceptions=True)
    out: dict = {"kind": kind, "units": "prob = implied chance 0-1; kalshi volume = contracts; polymarket volume = USD"}
    table: dict[str, dict] = {}
    if isinstance(k_evs, Exception) or not k_evs:
        out["kalshi"] = {"error": str(k_evs) if isinstance(k_evs, Exception) else f"no open {series} event"}
    else:
        ev = kalshi.event(max(k_evs, key=lambda e: sum(float(m.get("volume_fp") or 0) for m in e.get("markets") or [])))
        out["kalshi"] = {k: ev.get(k) for k in ("event_id", "title", "url", "volume")}
        for m in ev["markets"]:
            if (k := team_key(m.get("label"))):
                table.setdefault(k, {})["kalshi"] = drop_none({"prob": m.get("prob"), "volume": m.get("volume"),
                                                               "change_24h": m.get("change_24h"), "market_id": m.get("market_id")})
    p_ev = None if isinstance(p_evs, Exception) else next((e for e in p_evs if re.search(pattern, e.get("title", ""))), None)
    if p_ev is None:
        out["polymarket"] = {"error": str(p_evs) if isinstance(p_evs, Exception) else "no matching open event"}
    else:
        ev = polymarket.event(p_ev)
        out["polymarket"] = {k: ev.get(k) for k in ("event_id", "title", "url", "volume_usd", "volume_24h_usd")}
        for m in ev["markets"]:
            if (k := team_key(m.get("label"))):
                table.setdefault(k, {})["polymarket"] = drop_none({"prob": m.get("prob"), "volume_usd": m.get("volume_usd"),
                                                                   "change_24h": m.get("change_24h"), "market_id": m.get("market_id")})
    rows = []
    for k, sides in table.items():
        if want and k != want:
            continue
        ps = [s["prob"] for s in sides.values() if s.get("prob") is not None]
        rows.append({"team": FULL[k], **sides, "_avg": sum(ps) / len(ps) if ps else 0})
    rows.sort(key=lambda r: -r["_avg"])
    out["teams"] = [{k: v for k, v in r.items() if k != "_avg"} for r in rows]
    return out
