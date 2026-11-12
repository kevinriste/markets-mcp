"""Shared helpers: a small TTL-cached HTTP client, number shaping, slugs, time and downsampling."""
import asyncio
import datetime as dt
import json
import re
import time
from typing import Any

import httpx


class Http:
    """GET-only JSON client with a short TTL cache, so a chat that asks three questions about one game costs one fetch."""

    def __init__(self, ttl: float = 30.0, max_entries: int = 500):
        self.client = httpx.AsyncClient(timeout=20, follow_redirects=True,
                                        headers={"User-Agent": "markets-mcp/0.1", "Accept": "application/json"})
        self.ttl, self.max_entries = ttl, max_entries
        self.cache: dict[tuple, tuple[float, Any]] = {}

    async def get(self, url: str, params: dict | None = None) -> Any:
        key = (url, tuple(sorted((params or {}).items())))
        hit = self.cache.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        for attempt in range(3):
            r = await self.client.get(url, params=params)
            if r.status_code != 429 and r.status_code < 500:
                break
            await asyncio.sleep(1 + attempt)
        r.raise_for_status()
        data = r.json()
        if len(self.cache) >= self.max_entries:
            self.cache.clear()
        self.cache[key] = (time.monotonic(), data)
        return data


def num(v: Any) -> float | None:
    """Venue numbers arrive as floats, ints or strings ("0.6700", "1579385.36"); empty means unknown."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def prob(v: Any) -> float | None:
    f = num(v)
    return None if f is None else round(f, 3)


def vol(v: Any) -> int | None:
    f = num(v)
    return None if f is None else int(round(f))


def jlist(v: Any) -> list:
    """Polymarket encodes outcomes / prices / token ids as JSON strings inside the JSON."""
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.startswith("["):
        try:
            return json.loads(v)
        except ValueError:
            return []
    return []


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "market"


def iso(ts: float | int | None) -> str | None:
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None and v != [] and v != ""}


def downsample(points: list, n: int) -> list:
    """Keep at most n points, evenly spaced, always keeping the first and the last."""
    if len(points) <= n or n < 2:
        return points
    step = (len(points) - 1) / (n - 1)
    return [points[round(i * step)] for i in range(n)]


def bucket(rows: list[tuple[str, float, float]], n: int) -> list[tuple[str, float, float]]:
    """(time, price, volume) candles merged into at most n consecutive buckets: the bucket's last time and price, its
    summed volume (so no traded volume is dropped, unlike downsampling)."""
    if len(rows) <= n or n < 1:
        return rows
    size = -(-len(rows) // n)
    return [(chunk[-1][0], chunk[-1][1], sum(v for *_, v in chunk))
            for chunk in (rows[i:i + size] for i in range(0, len(rows), size))]


def summarize(series: list[tuple[str, float]]) -> dict:
    """start/end/change/high/low of a (time, probability) series."""
    if not series:
        return {}
    ps = [p for _, p in series]
    return {"start": {"t": series[0][0], "prob": round(ps[0], 3)}, "end": {"t": series[-1][0], "prob": round(ps[-1], 3)},
            "change": round(ps[-1] - ps[0], 3), "high": round(max(ps), 3), "low": round(min(ps), 3)}


def closest_to_even(markets: list[dict], key: str = "prob") -> dict | None:
    """The main line of a spread/total ladder: the traded rung priced nearest 50%."""
    traded = [m for m in markets if m.get(key) is not None and (m.get("volume") or m.get("volume_usd") or 0) > 0]
    pool = traded or [m for m in markets if m.get(key) is not None]
    return min(pool, key=lambda m: abs(m[key] - 0.5)) if pool else None
