# markets-mcp

Read-only prediction-market odds over MCP, for Calliope: **Polymarket** and **Kalshi**, keyless public APIs.
Every result carries the venue's own page `url` (so the model can hand the user a link), volume, and the ids
to drill down (`event_id` -> `get_event`, `market_id` -> `price_history`).

| tool | what |
| --- | --- |
| `search_markets(query, venue, limit, include_closed)` | any topic on both venues, biggest volume first |
| `get_event(venue, event_id, market_filter, max_markets, sort)` | one event: every market's prob, bid/ask, 24h change, volume, OI |
| `price_history(venue, market_id, window, outcome)` | 1d / 1w / 1m / all: points + start/end/change/high/low; Kalshi adds volume per bucket |
| `nfl_games(team, days)` | the week's games, both venues joined: moneyline, main spread, main total |
| `nfl_futures(kind, team)` | champion / afc / nfc / playoffs, one row per team, both venues |

Units: `prob` is the implied chance 0-1 (Kalshi: bid/ask mid when the spread is <= 10c, else last trade;
Polymarket: its displayed price). Kalshi volume = contracts ($1 payout each), Polymarket volume = USD.

Links: Kalshi `https://kalshi.com/markets/<series>/<any-slug>/<event_ticker>` (the slug is cosmetic, the site
redirects to its own); Polymarket `https://polymarket.com/event/<slug>`.

## Run

```bash
docker compose up -d --build     # joins realtime-android_default; no ports published, no secrets
```

Calliope registers it as `http://markets-mcp:8000/mcp` (conversation-service `/mcp/servers`). Tests are pure
shaping (no network): `CI_ROOT=~/dev/markets-mcp ~/dev/realtime-android/scripts/ci-remote.sh py . -q`.
For a live check, any MCP client against the container's IP on that network.
