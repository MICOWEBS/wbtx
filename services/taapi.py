import aiohttp
from functools import lru_cache
from typing import Optional
import logging

from config import env
from core.cache import ttl_cache
from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)

BASE_URL = "https://api.taapi.io"
DEFAULT_SYMBOL = "WBTC/USDT"
DEFAULT_EXCHANGE = "binance"
DEFAULT_INTERVAL = "1m"


async def _fetch(indicator: str, symbol: str = DEFAULT_SYMBOL, exchange: str = DEFAULT_EXCHANGE,
                 interval: str = DEFAULT_INTERVAL) -> Optional[float]:
    """Low-level async fetch from TAAPI.io API."""
    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    async def _inner():
        if not env.TAAPI_SECRET:
            logger.error("TAAPI_SECRET not configured")
            return None

        params = {
            "secret": env.TAAPI_SECRET,
            "exchange": exchange,
            "symbol": symbol,
            "interval": interval,
        }
        url = f"{BASE_URL}/{indicator}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"TAAPI error: {resp.status} - {error_text}")
                        return None
                    
                    payload = await resp.json()
                    if "error" in payload:
                        logger.error(f"TAAPI error: {payload['error']}")
                        return None
                    
                    # Handle different response formats
                    if indicator == "macd":
                        return payload
                    
                    # For other indicators, try different possible value keys
                    for key in ["value", "valueRSI", "valueEMA"]:
                        if key in payload:
                            return payload[key]
                    
                    logger.error(f"TAAPI missing value in response: {payload}")
                    return None
                    
        except aiohttp.ClientError as e:
            logger.error(f"TAAPI request failed: {str(e)}")
            return None
        except asyncio.TimeoutError:
            logger.error("TAAPI request timed out")
            return None
        except Exception as e:
            logger.error(f"Unexpected TAAPI error: {str(e)}")
            return None

    try:
        return await _inner()
    except Exception as e:
        logger.error(f"TAAPI fetch failed for {indicator}: {str(e)}")
        return None


@ttl_cache(ttl=30)
async def get_rsi(symbol: str = DEFAULT_SYMBOL, interval: str = DEFAULT_INTERVAL) -> Optional[float]:
    value = await _fetch("rsi", symbol=symbol, interval=interval)
    if value is None:
        logger.warning(f"Failed to fetch RSI for {symbol}")
    return value


@ttl_cache(ttl=30)
async def get_ema(symbol: str = DEFAULT_SYMBOL, interval: str = DEFAULT_INTERVAL, length: int = 14) -> Optional[float]:
    # Use the correct endpoint for EMA
    value = await _fetch("ema", symbol=symbol, interval=interval)
    if value is None:
        logger.warning(f"Failed to fetch EMA for {symbol}")
    return value


@ttl_cache(ttl=30)
async def get_macd_hist(symbol: str = DEFAULT_SYMBOL, interval: str = DEFAULT_INTERVAL) -> Optional[float]:
    data = await _fetch("macd", symbol=symbol, interval=interval)
    if data is None:
        logger.warning(f"Failed to fetch MACD for {symbol}")
        return None
        
    # Handle the new MACD response format
    if isinstance(data, dict):
        hist = data.get("valueMACDHist")
        if hist is None:
            logger.warning(f"MACD response missing histogram: {data}")
        return hist
    return data 