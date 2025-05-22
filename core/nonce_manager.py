import asyncio
from typing import Dict
from web3 import Web3

class NonceManager:
    _instances: Dict[str, "NonceManager"] = {}

    def __init__(self, w3: Web3, address: str):
        self.w3 = w3
        self.address = address
        self._nonce = self.w3.eth.get_transaction_count(self.address)
        self._lock = asyncio.Lock()

    @classmethod
    def for_account(cls, w3: Web3, address: str) -> "NonceManager":
        if address not in cls._instances:
            cls._instances[address] = cls(w3, address)
        return cls._instances[address]

    async def next_nonce(self) -> int:
        async with self._lock:
            current = self._nonce
            self._nonce += 1
            return current 