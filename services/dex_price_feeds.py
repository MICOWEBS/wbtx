import asyncio
import aiohttp
from typing import Optional, Dict

from core.cache import ttl_cache
from core.constants import WBTC_ADDRESS, USDT_ADDRESS

DEX_SCREENER_BASE = "https://api.dexscreener.com/latest/dex/pairs"

DEX_ENDPOINTS: Dict[str, str] = {
    "pancake": f"bsc/{WBTC_ADDRESS}",  # PancakeSwap v2 auto pair lookup
}


async def _fetch_dexscreener(path: str) -> Optional[float]:
    url = f"{DEX_SCREENER_BASE}/{path}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=10) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            price = data.get("pairs", [{}])[0].get("priceUsd")
            return float(price) if price else None


@ttl_cache(ttl=20)
async def get_price(dex: str = "pancake") -> Optional[float]:
    """Get current WBTC price in USDT from DEX."""
    try:
        # Use DexScreener as fallback
        return await get_price_from_dexscreener(dex)
    except Exception as e:
        print(f"Error getting price: {e}")
        return None


@ttl_cache(ttl=30)
async def get_price_from_dexscreener(dex: str) -> Optional[float]:
    path = DEX_ENDPOINTS.get(dex)
    if not path:
        return None
    return await _fetch_dexscreener(path) 