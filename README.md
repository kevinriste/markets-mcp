# markets-mcp

An MCP server for read-only prediction-market odds from **Polymarket** and **Kalshi**. Both venues' public
APIs are keyless, so the server needs no account, key or secret.

Every result carries the venue's own page `url` (so a model can hand the user a link), the volume, and the
ids needed to drill down (`event_id` → `get_event`, `market_id` → `price_history`).

## Tools

| tool | what |
| --- | --- |
| `search_markets(query, venue, limit, include_closed)` | any topic on both venues, biggest volume first |
| `get_event(venue, event_id, market_filter, max_markets, sort)` | one event: every market's prob, bid/ask, 24h change, volume, open interest |
| `price_history(venue, market_id, window, outcome)` | `1d` / `1w` / `1m` / `all`: points plus start/end/change/high/low; Kalshi adds volume per bucket |
| `nfl_games(team, days)` | the coming NFL games, both venues joined: moneyline, main spread, main total |
| `nfl_futures(kind, team)` | `champion` / `afc` / `nfc` / `playoffs`, one row per team, both venues |

**Units.**
- `prob` is the implied chance of the outcome, from 0 to 1.
  - Kalshi: the bid/ask midpoint when the spread is 10¢ or less, otherwise the last trade.
  - Polymarket: its displayed price.
- Volume units differ, so never add them together: Kalshi counts contracts traded (each pays $1), and Polymarket counts USD.

**Links.**
- Kalshi: `https://kalshi.com/markets/<series>/<any-slug>/<event_ticker>`. The slug is cosmetic; the site redirects to its own.
- Polymarket: `https://polymarket.com/event/<slug>`.

## Run

With Docker, served as Streamable HTTP at `http://127.0.0.1:8000/mcp`:

```bash
docker compose up -d --build          # MARKETS_MCP_PORT=8766 to publish another host port
```

The compose file publishes on loopback only. The server has no authentication, so keep it off public
interfaces; to reach it from another stack, join that stack's Docker network instead (a git-ignored
`docker-compose.override.yml` is merged in automatically for that kind of local wiring).

Without Docker (Python 3.12):

```bash
pip install -r requirements.txt
PYTHONPATH=src python -m markets_mcp                                  # stdio
PYTHONPATH=src python -m markets_mcp --http --host 127.0.0.1 --port 8000
```

### Calling it

Any MCP client works. The HTTP transport is stateless, so a single JSON-RPC POST is a complete call; the
`Accept` header must list both `application/json` and `text/event-stream`:

```bash
curl -s http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"nfl_games","arguments":{"team":"BAL","days":8}}}'
```

## Tests

The tests cover result shaping only and make no network calls:

```bash
pip install -r requirements.txt pytest
pytest -q
```

## Notes

- The venues' APIs are public but rate-limited. Each GET is cached for 30 seconds, so repeated questions about
  one game cost one fetch, and a 429 or 5xx is retried twice with a short backoff.
- DNS-rebinding protection is off so other containers can reach the server by container name. Everything it
  serves is public, read-only market data, but that is one more reason not to expose it beyond your own hosts.
- Prices are live market data, not advice, and they move. Cite the time you read them when it matters.
