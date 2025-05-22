import asyncio
import json
import logging
import time
from typing import Optional, Dict, Tuple, List
from decimal import Decimal

from web3 import Web3, HTTPProvider
from web3.middleware import geth_poa_middleware
from eth_account import Account
from eth_typing import Address
from eth_utils import to_checksum_address

from config import env
from core.logger import get_logger
from core.constants import WBTC_ADDRESS, USDT_ADDRESS, PANCAKE_ROUTER_ADDRESS, ROUTER_BY_DEX, WBNB_ADDRESS
from db.models import log_trade, log_error, insert_position
from db import models
from core.nonce_manager import NonceManager
from services.dex_price_feeds import get_price

logger = get_logger(__name__)

# Token decimals (BSC mainnet values)
USDT_DECIMALS = 18  # BSC USDT uses 18 decimals
WBTC_DECIMALS = 18   # BSC WBTC uses 18 decimals
WBNB_DECIMALS = 18  # BSC WBNB uses 18 decimals

# === Constants (BSC Mainnet) === #


# Minimal ERC20 ABI (balanceOf, allowance, approve, decimals)
ERC20_ABI: List[dict] = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
]


# Minimal Router ABI (swapExactTokensForTokens + getAmountsOut)
ROUTER_ABI: List[dict] = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactTokensForTokens",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
        ],
        "name": "getAmountsOut",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function",
    },
]


class TradeExecutor:
    """Handles on-chain execution of buy/sell orders on PancakeSwap"""

    def __init__(self, dex: str = "pancake", w3: Web3 | None = None):
        self.w3 = w3 or Web3(HTTPProvider(env.BSC_RPC_URL))
        # BSC uses proof-of-authority
        self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        self.account = self.w3.eth.account.from_key(env.PRIVATE_KEY)
        logger.info(f"Loaded wallet {self.account.address}")

        self.nonce_manager = NonceManager.for_account(self.w3, self.account.address)

        router_addr = ROUTER_BY_DEX.get(dex, PANCAKE_ROUTER_ADDRESS)
        self.router = self.w3.eth.contract(address=router_addr, abi=ROUTER_ABI)
        self.btcb = self.w3.eth.contract(address=WBTC_ADDRESS, abi=ERC20_ABI)  # BTCB contract
        self.usdt = self.w3.eth.contract(address=USDT_ADDRESS, abi=ERC20_ABI)
        self.wbnb = self.w3.eth.contract(address=WBNB_ADDRESS, abi=ERC20_ABI)

        # Cache decimals for conversion - use same parameters as router_quotes.py
        self.dec_btcb = self.btcb.functions.decimals().call({'from': '0x0000000000000000000000000000000000000000'})
        self.dec_usdt = self.usdt.functions.decimals().call({'from': '0x0000000000000000000000000000000000000000'})
        self.dec_wbnb = self.wbnb.functions.decimals().call({'from': '0x0000000000000000000000000000000000000000'})
        
        # Validate decimals
        if self.dec_usdt != 18:
            logger.warning(f"⚠️ USDT decimals mismatch: got {self.dec_usdt}, expected 18!")
            # Force to 18 decimals for BSC USDT
            self.dec_usdt = 18
            logger.info("Using 18 decimals for USDT (standard for BSC USDT)")
        
        if self.dec_wbnb != 18:
            logger.warning(f"⚠️ WBNB decimals mismatch: got {self.dec_wbnb}, expected 18!")
            # Force to 18 decimals for BSC WBNB
            self.dec_wbnb = 18
            logger.info("Using 18 decimals for WBNB (standard for BSC WBNB)")
        
        logger.info(f"Token decimals from contracts - USDT: {self.dec_usdt}, BTCB: {self.dec_btcb}")
        logger.info(f"BTCB contract address: {WBTC_ADDRESS}")
        logger.info(f"USDT contract address: {USDT_ADDRESS}")
        logger.info(f"Using decimals - USDT: {self.dec_usdt}, BTCB: {self.dec_btcb}")

    # ------------------------------------------------------------------
    async def execute(self, signal: Dict):
        """Public entry point called by the bot loop."""
        try:
            action = signal.get("action")
            if action not in {"buy", "sell"}:
                logger.warning(f"Unknown action {action}")
                return

            logger.info(f"→ Executing {action.upper()} order …")
            if action == "buy":
                tx_hash, qty_out, expect = await self._buy_btcb(signal)
                profit_usd = 0
                await log_trade(
                    "buy",
                    float(qty_out),
                    signal.get("dex_price"),
                    0,
                    0,
                    tx_hash,
                    profit_usd=profit_usd,
                    expected_out=expect,
                )
                await insert_position("long", signal.get("dex_price"), float(qty_out))
                # Background trailing-stop watcher
                asyncio.create_task(
                    self._monitor_trailing_stop(entry_price=signal.get("dex_price"))
                )
            else:  # sell
                tx_hash, usdt_out, wbtc_in, expect = await self._sell_btcb(signal)
                profit_pct = (
                    (usdt_out - wbtc_in) / wbtc_in * 100 if wbtc_in else 0
                )
                profit_usd = float(usdt_out) - float(self._from_wei(wbtc_in, self.dec_btcb) * signal.get("dex_price", 0))
                await log_trade(
                    "sell",
                    float(self._from_wei(wbtc_in, self.dec_btcb)),
                    0,
                    signal.get("dex_price"),
                    profit_pct,
                    tx_hash,
                    profit_usd=profit_usd,
                    expected_out=expect,
                )

        except Exception as exc:
            logger.exception(exc)
            await log_error("trade_executor", str(exc))

    # ------------------------------------------------------------------
    async def _buy_btcb(self, signal: Dict):
        """Swap USDT → BTCB proportionally to MAX_TRADE_PERCENT."""
        balance_usdt = self.usdt.functions.balanceOf(self.account.address).call()
        if balance_usdt == 0:
            raise RuntimeError("USDT balance is zero – cannot buy")

        amount_pct = signal.get("position_pct", env.MAX_TRADE_PERCENT) / 100.0
        amount_in = int(balance_usdt * amount_pct)
        await self._ensure_allowance(self.usdt, amount_in)

        # Price-impact aware minOut using router quote
        path = [USDT_ADDRESS, WBNB_ADDRESS, WBTC_ADDRESS]  # USDT -> WBNB -> BTCB
        quoted = self.router.functions.getAmountsOut(amount_in, path).call()
        expected_out = quoted[-1]
        min_out = int(expected_out * (1 - env.SLIPPAGE_TOLERANCE / 100))

        # Log the amounts for debugging
        logger.info(f"Buy amounts - USDT in: {self._from_wei(amount_in, self.dec_usdt):.2f}, "
                   f"BTCB out: {self._from_wei(expected_out, self.dec_btcb):.8f}")

        deadline = int(time.time()) + 60
        build_tx = self.router.functions.swapExactTokensForTokens(
            amount_in,
            min_out,
            path,
            self.account.address,
            deadline,
        )
        tx_params = await self._build_tx_params()
        txn = build_tx.build_transaction(tx_params)

        gas_estimate = self.w3.eth.estimate_gas(txn)
        fee_bnb = self.w3.from_wei(int(txn["gasPrice"] * gas_estimate), 'ether')
        if fee_bnb > env.MAX_GAS_FEE_BNB:
            raise RuntimeError(f"Gas fee {fee_bnb} BNB exceeds max {env.MAX_GAS_FEE_BNB}")

        txn["gas"] = gas_estimate

        signed = self.account.sign_transaction(txn)
        tx_hash = await self._send_tx(signed)
        logger.info(f"Buy tx → {tx_hash.hex()}")

        return tx_hash.hex(), self._from_wei(min_out, self.dec_btcb), self._from_wei(expected_out, self.dec_btcb)

    async def _sell_btcb(self, signal: Dict):
        """Swap BTCB → USDT proportionally to MAX_TRADE_PERCENT."""
        balance_btcb = self.btcb.functions.balanceOf(self.account.address).call()
        if balance_btcb == 0:
            raise RuntimeError("BTCB balance is zero – cannot sell")

        amount_pct = signal.get("position_pct", env.MAX_TRADE_PERCENT) / 100.0
        amount_in = int(balance_btcb * amount_pct)
        await self._ensure_allowance(self.btcb, amount_in)

        # Price-impact aware minOut using router quote
        path = [WBTC_ADDRESS, WBNB_ADDRESS, USDT_ADDRESS]  # BTCB -> WBNB -> USDT
        quoted = self.router.functions.getAmountsOut(amount_in, path).call()
        expected_out = quoted[-1]
        min_out = int(expected_out * (1 - env.SLIPPAGE_TOLERANCE / 100))

        # Log the amounts for debugging
        logger.info(f"Sell amounts - BTCB in: {self._from_wei(amount_in, self.dec_btcb):.8f}, "
                   f"USDT out: {self._from_wei(expected_out, self.dec_usdt):.2f}")

        deadline = int(time.time()) + 60
        build_tx = self.router.functions.swapExactTokensForTokens(
            amount_in,
            min_out,
            path,
            self.account.address,
            deadline,
        )
        tx_params = await self._build_tx_params()
        txn = build_tx.build_transaction(tx_params)

        gas_estimate = self.w3.eth.estimate_gas(txn)
        fee_bnb = self.w3.from_wei(int(txn["gasPrice"] * gas_estimate), 'ether')
        if fee_bnb > env.MAX_GAS_FEE_BNB:
            raise RuntimeError(f"Gas fee {fee_bnb} BNB exceeds max {env.MAX_GAS_FEE_BNB}")

        txn["gas"] = gas_estimate

        signed = self.account.sign_transaction(txn)
        tx_hash = await self._send_tx(signed)
        logger.info(f"Sell tx → {tx_hash.hex()}")

        return tx_hash.hex(), self._from_wei(min_out, self.dec_usdt), amount_in, self._from_wei(expected_out, self.dec_usdt)

    async def sell_exact(self, amount_wbtc: float, current_price: float):
        """Sell a fixed BTCB amount (in token units) used by TP watcher."""
        amt_wei = int(amount_wbtc * (10 ** self.dec_btcb))
        await self._ensure_allowance(self.btcb, amt_wei)

        # quote expected out
        path = [WBTC_ADDRESS, USDT_ADDRESS]
        quoted = self.router.functions.getAmountsOut(amt_wei, path).call()
        expected_out = quoted[-1]
        min_out = int(expected_out * (1 - env.SLIPPAGE_TOLERANCE / 100))

        deadline = int(time.time()) + 60
        build_tx = self.router.functions.swapExactTokensForTokens(
            amt_wei,
            min_out,
            path,
            self.account.address,
            deadline,
        )
        txn = build_tx.build_transaction(await self._build_tx_params())
        gas_estimate = self.w3.eth.estimate_gas(txn)
        txn["gas"] = gas_estimate
        signed = self.account.sign_transaction(txn)
        tx_hash = await self._send_tx(signed)

        profit_pct = (self._from_wei(expected_out, self.dec_usdt) - amount_wbtc * current_price) / (amount_wbtc * current_price) * 100
        await log_trade(
            "sell", amount_wbtc, 0, current_price, profit_pct, tx_hash, expected_out=self._from_wei(expected_out, self.dec_usdt)
        )
        return tx_hash.hex()

    # ------------------------------------------------------------------
    async def _ensure_allowance(self, token_contract, needed_amount):
        allowance = token_contract.functions.allowance(
            self.account.address, PANCAKE_ROUTER_ADDRESS
        ).call()
        if allowance >= needed_amount:
            return
        logger.info("Approving router spending …")
        build = token_contract.functions.approve(PANCAKE_ROUTER_ADDRESS, 2 ** 256 - 1)
        tx = build.build_transaction(await self._build_tx_params())
        signed = self.account.sign_transaction(tx)
        await self._send_tx(signed)

    async def _build_tx_params(self):
        current_gwei = self.w3.from_wei(self.w3.eth.gas_price, 'gwei')
        if current_gwei > env.MAX_GAS_PRICE_GWEI:
            raise RuntimeError(f"Gas price too high: {current_gwei} gwei > {env.MAX_GAS_PRICE_GWEI}")

        return {
            "from": self.account.address,
            "nonce": await self.nonce_manager.next_nonce(),
            "gasPrice": self.w3.to_wei(env.MAX_GAS_PRICE_GWEI, 'gwei') if current_gwei > env.MAX_GAS_PRICE_GWEI else self.w3.eth.gas_price,
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _from_wei(amount_wei: int, decimals: int):
        return amount_wei / (10 ** decimals)

    # ------------------------------------------------------------------
    async def _monitor_trailing_stop(self, entry_price: float):
        """Continuously checks price; sells when trailing stop triggers."""
        stop_fraction = env.TRAILING_STOP_PERCENT / 100
        peak = entry_price
        logger.info(
            f"Trailing-stop armed at {stop_fraction*100:.2f}% | entry={entry_price:.2f}"
        )

        from services.dex_price_feed import get_dex_price

        while True:
            current = await get_dex_price()
            if current is None:
                await asyncio.sleep(env.SIGNAL_INTERVAL)
                continue

            if current > peak:
                peak = current

            # Hard stop-loss from entry
            if current <= entry_price * (1 - env.HARD_STOP_LOSS_PERCENT / 100):
                logger.info("Hard stop-loss triggered – executing sell …")
                await self.execute({"action": "sell", "dex_price": current})
                break

            if current <= peak * (1 - stop_fraction):
                logger.info(
                    f"Trailing stop fired → price {current:.2f} (peak {peak:.2f})"
                )
                await self.execute({"action": "sell", "dex_price": current})
                break

            await asyncio.sleep(env.SIGNAL_INTERVAL)

    async def _send_tx(self, signed):
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        await models.insert_pending(tx_hash.hex(), signed.transaction.nonce, signed.transaction.gasPrice)
        return tx_hash 