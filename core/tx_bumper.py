import asyncio
import logging
from typing import Optional
from datetime import datetime, timezone

from db.models import pending_rows, mark_mined, update_bump
from config import env
from core.logger import get_logger
from web3 import Web3, HTTPProvider

logger = get_logger(__name__)

w3 = Web3(HTTPProvider(env.BSC_RPC_URL))


async def bump_loop():
    while True:
        rows = await pending_rows()
        for row in rows:
            tx_hash = row["tx_hash"]
            receipt = w3.eth.get_transaction_receipt(tx_hash) if w3.eth.get_transaction_receipt else None
            if receipt and receipt.status == 1:
                await mark_mined(tx_hash)
                logger.info(f"Tx {tx_hash} mined")
                continue
            # check timeout
            age = (datetime.now(timezone.utc) - row["sent_at"]).total_seconds()
            if age > env.TX_TIMEOUT_SEC and row["bumps"] < env.MAX_BUMPS:
                raw = w3.eth.get_transaction(tx_hash)
                new_gas = int(raw["gasPrice"] * env.GAS_BUMP_FACTOR)
                new_tx = raw.copy()
                new_tx["gasPrice"] = new_gas
                signed = w3.eth.account.sign_transaction(new_tx, env.PRIVATE_KEY)
                new_hash = w3.eth.send_raw_transaction(signed.rawTransaction).hex()
                await update_bump(tx_hash, new_hash, new_gas, row["bumps"] + 1)
                logger.info(f"Bumped tx {tx_hash} -> {new_hash}")
        await asyncio.sleep(30) 