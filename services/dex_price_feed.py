import aiohttp
from typing import Optional
from tenacity import retry, stop_after_attempt, wait_fixed

from backend.core.cache import ttl_cache

WBTC_ADDRESS = "0x7130d2A12B9ACbfF4F2634d864A1Ee1Ce3Ead9c"  # WBTC token on BSC
BASE_URL = "https://api.pancakeswap.info/api/v2/tokens/{}"


@ttl_cache(ttl=30)
async def get_dex_price(token_address: str = WBTC_ADDRESS) -> Optional[float]:
    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    async def _inner():
        url = BASE_URL.format(token_address)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                resp.raise_for_status()
                data = await resp.json()
                price = data["data"]["price"]
                return float(price)

    try:
        return await _inner()
    except Exception:
        return None 