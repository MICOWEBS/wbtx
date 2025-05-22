import asyncio
import time
from db.models import list_positions, update_position_qty, delete_position
from config import env
from core.logger import get_logger
from services.dex_price_feeds import get_price
from core.trade_executor import TradeExecutor

logger = get_logger(__name__)

async def tp_loop():
    exec = TradeExecutor()
    while True:
        positions = await list_positions()
        for pos in positions:
            price = await get_price('pancake')
            if not price:
                continue
            entry = pos['entry_price']
            qty_left = pos['qty_left']
            tp1 = entry * 1.008
            tp2 = entry * 1.015
            tp3 = entry * 1.03
            if not pos['tp1_hit'] and price >= tp1:
                sell_qty = qty_left * 0.5
                await exec.sell_exact(sell_qty, price)
                await update_position_qty(pos['id'], qty_left - sell_qty, True, pos['tp2_hit'])
                logger.info(f"TP1 hit, sold {sell_qty}")
            elif pos['tp1_hit'] and not pos.get('tp2_hit') and price >= tp2:
                sell_qty = qty_left * 0.5
                await exec.sell_exact(sell_qty, price)
                await update_position_qty(pos['id'], qty_left - sell_qty, True, True)
                logger.info("TP2 hit, scaled out second 50%")
            elif pos.get('tp2_hit') and price >= tp3:
                await exec.sell_exact(qty_left, price)
                await delete_position(pos['id'])
                logger.info("TP3 hit, position closed")
        await asyncio.sleep(30) 