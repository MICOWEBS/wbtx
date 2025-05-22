import aiohttp
from typing import Optional
from tenacity import retry, stop_after_attempt, wait_fixed

from core.cache import ttl_cache

BASE_URL = "https://api.binance.com/api/v3/ticker/price"
SYMBOL = "WBTCUSDT"


@ttl_cache(ttl=30)
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
async def get_spot_price(symbol: str = SYMBOL) -> Optional[float]:
    async def _inner():
        params = {"symbol": symbol}
        async with aiohttp.ClientSession() as session:
            async with session.get(BASE_URL, params=params, timeout=10) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return float(data["price"])

    try:
        return await _inner()
    except Exception:
        return None 