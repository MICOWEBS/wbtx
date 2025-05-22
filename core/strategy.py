import math
import asyncio
from typing import Optional, Dict, List, Tuple

from services import taapi, binance_feed
from services import router_quotes
from config import env
from core.logger import get_logger

logger = get_logger(__name__)

BUY = "buy"
SELL = "sell"


async def evaluate() -> Optional[dict]:
    """Evaluate market and return dict with action and metrics or None"""
    # Fetch concurrently
    rsi_task = taapi.get_rsi()
    ema_task = taapi.get_ema()
    macd_task = taapi.get_macd_hist() if env.MAC_D_FILTER else asyncio.sleep(0, result=None)
    binance_task = binance_feed.get_spot_price()
    # determine quotes for a standard amount 100 USDT (in base units)
    amount_in = 100.0  # Changed from 100 * (10 ** 18)
    quote_task = router_quotes.best_buy_quote(amount_in)  # Remove asyncio.to_thread since function is now async

    # Check wallet balances
    from services.wallet_balances import get_balances
    from core.trade_executor import TradeExecutor
    executor = TradeExecutor()
    balances = await get_balances(executor.account.address)
    usdt_balance = float(balances.get('USDT', 0))
    btc_balance = float(balances.get('BTC', 0))

    rsi, ema, binance_price, quote_result, macd_hist = await asyncio.gather(
        rsi_task, ema_task, binance_task, quote_task, macd_task
    )

    if None in (rsi, ema, binance_price) or not quote_result:
        logger.warning("Indicator fetch failed")
        return None

    best_buy_dex, best_buy_price = quote_result
    # for sell, you could compute similar but for simplicity reuse best_buy_dex valuations
    best_sell_dex, best_sell_price = best_buy_dex, best_buy_price

    # spread relative to Binance for chosen side
    buy_spread = (best_buy_price - binance_price) / binance_price * 100
    sell_spread = (best_sell_price - binance_price) / binance_price * 100

    # Log current market conditions
    logger.info(f"Market conditions - RSI: {rsi:.2f}, EMA: {ema:.2f}, Binance: {binance_price:.2f}, DEX: {best_buy_price:.2f}")
    logger.info(f"Spreads - Buy: {buy_spread:.2f}%, Sell: {sell_spread:.2f}%")
    logger.info(f"Balances - USDT: {usdt_balance:.2f}, BTC: {btc_balance:.8f}")
    if macd_hist is not None:
        logger.info(f"MACD Histogram: {macd_hist:.2f}")

    # Create a list of DEX options with their prices
    dex_prices: List[Tuple[str, float]] = [(best_buy_dex, best_buy_price)]
    # Sort by price (ascending)
    dex_prices.sort(key=lambda x: x[1])
    # Extract just the DEX names in price order
    dex_options = [dex for dex, _ in dex_prices]

    # Calculate MACD threshold as percentage of price
    # Using 0.1% of price as threshold, with minimum of 10 and maximum of 100
    macd_threshold = max(min(binance_price * 0.001, 100), 10)

    # Modified conditions with improved thresholds
    # RSI zones with buffer to reduce chop
    rsi_ok_buy = rsi < 45  # More conservative buy zone
    rsi_ok_sell = rsi > 55  # More conservative sell zone

    # MACD conditions with dynamic threshold
    # If MACD is None, only allow trades if spread is very good (>1%)
    macd_ok_buy = (macd_hist is None and buy_spread > 1.0) or (macd_hist is not None and macd_hist > -macd_threshold)
    macd_ok_sell = (macd_hist is None and sell_spread > 1.0) or (macd_hist is not None and macd_hist < macd_threshold)

    # Log buy conditions
    buy_conditions = {
        "Spread >= 0.5%": buy_spread >= 0.5,
        "RSI < 45": rsi_ok_buy,
        "MACD OK": macd_ok_buy,
        "MACD Threshold": f"{macd_threshold:.2f}",
        "USDT Balance > 0": usdt_balance > 0
    }
    logger.info(f"Buy conditions: {buy_conditions}")

    # Log sell conditions
    sell_conditions = {
        "Spread >= 0.5%": sell_spread >= 0.5,
        "RSI > 55": rsi_ok_sell,
        "MACD OK": macd_ok_sell,
        "MACD Threshold": f"{macd_threshold:.2f}",
        "BTC Balance > 0": btc_balance > 0
    }
    logger.info(f"Sell conditions: {sell_conditions}")

    # Buy Conditions - focus on spread with risk filters
    if buy_spread >= 0.5 and rsi_ok_buy and macd_ok_buy and usdt_balance > 0:
        logger.info("BUY signal triggered")
        return {
            "action": BUY,
            "rsi": rsi,
            "ema": ema,
            "price_spread": buy_spread,
            "dex_price": best_buy_price,
            "dex_source": best_buy_dex,
            "binance_price": binance_price,
            "dex_options": dex_options,
        }

    # Sell Conditions - focus on spread with risk filters
    if sell_spread >= 0.5 and rsi_ok_sell and macd_ok_sell and btc_balance > 0:
        logger.info("SELL signal triggered")
        return {
            "action": SELL,
            "rsi": rsi,
            "ema": ema,
            "price_spread": sell_spread,
            "dex_price": best_sell_price,
            "dex_source": best_sell_dex,
            "binance_price": binance_price,
            "dex_options": dex_options[::-1],  # Reverse for sell to prioritize higher prices
        }

    logger.info("No trading signals generated")
    return None 