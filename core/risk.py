import math
from statistics import stdev
from typing import List

from db.models import Database
from config import env

DEFAULT_LOOKBACK = 20


def _fetch_recent_returns(n: int = DEFAULT_LOOKBACK) -> List[float]:
    # positive or negative percentage returns
    if Database._pool is None:
        return []

    # We'll query synchronously via run_until_complete later
    return []


async def recent_profit_percents(limit: int = DEFAULT_LOOKBACK) -> List[float]:
    if Database._pool is None:
        return []
    query = "SELECT profit FROM trades ORDER BY timestamp DESC LIMIT $1;"
    async with Database._pool.acquire() as conn:
        rows = await conn.fetch(query, limit)
        return [r["profit"] for r in rows]


async def dynamic_position_pct() -> float:
    profits = await recent_profit_percents()
    if len(profits) < 5:
        return env.MAX_TRADE_PERCENT

    vol = stdev(profits)
    # Scale size inversely with volatility; target risk of 1 unit
    if vol == 0:
        return env.MAX_TRADE_PERCENT

    k = 1 / (vol * 10)
    pct = max(env.MIN_TRADE_PERCENT, min(env.MAX_TRADE_PERCENT, env.MAX_TRADE_PERCENT * k))
    return pct


async def consecutive_losses() -> int:
    if Database._pool is None:
        return 0
    query = "SELECT profit_usd FROM trades ORDER BY timestamp DESC;"
    async with Database._pool.acquire() as conn:
        rows = await conn.fetch(query)
    count = 0
    for r in rows:
        if r["profit_usd"] < 0:
            count += 1
        else:
            break
    return count 