import aiohttp
from typing import Dict
from web3 import Web3, HTTPProvider
from fastapi import HTTPException, status
from web3.exceptions import ContractLogicError, BadFunctionCallOutput

from config import env
from core.constants import WBTC_ADDRESS, USDT_ADDRESS

ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
]

def _erc20_balance(w3: Web3, token_addr: str, wallet: str) -> float:
    try:
        # Check if we're connected to the right network
        chain_id = w3.eth.chain_id
        if chain_id != 56:  # BSC Mainnet
            print(f"Warning: Connected to chain ID {chain_id}, expected 56 (BSC Mainnet)")
            return 0.0

        token = w3.eth.contract(address=token_addr, abi=ERC20_ABI)
        balance = token.functions.balanceOf(wallet).call()
        decimals = token.functions.decimals().call()
        return balance / (10 ** decimals)
    except (ContractLogicError, BadFunctionCallOutput) as e:
        print(f"Error getting balance for token {token_addr}: {str(e)}")
        return 0.0
    except Exception as e:
        print(f"Unexpected error getting balance for token {token_addr}: {str(e)}")
        return 0.0

async def get_balances(wallet: str) -> Dict:
    try:
        # Initialize Web3 with timeout
        w3 = Web3(Web3.HTTPProvider(env.BSC_RPC_URL, request_kwargs={'timeout': 10}))
        
        # Check if connected to node
        if not w3.is_connected():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Unable to connect to BSC node"
            )
        
        # Check chain ID
        chain_id = w3.eth.chain_id
        if chain_id != 56:  # BSC Mainnet
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Connected to wrong network (chain ID: {chain_id}, expected: 56)"
            )
        
        # Ensure address is checksummed
        wallet = Web3.to_checksum_address(wallet.lower())
        
        # Get native token balance
        try:
            bnb_balance = w3.from_wei(w3.eth.get_balance(wallet), "ether")
        except Exception as e:
            print(f"Error getting BNB balance: {str(e)}")
            bnb_balance = 0
        
        # Get token balances
        wbtc_balance = _erc20_balance(w3, WBTC_ADDRESS, wallet)
        usdt_balance = _erc20_balance(w3, USDT_ADDRESS, wallet)

        # Optional: fetch USD price of BNB via BSCScan (skip if key missing)
        usd_prices = {}
        if env.BSCSCAN_API_KEY:
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"https://api.bscscan.com/api?module=stats&action=bnbprice&apikey={env.BSCSCAN_API_KEY}"
                    async with session.get(url, timeout=8) as resp:
                        data = await resp.json()
                        price = float(data.get("result", {}).get("ethusd", 0))
                        usd_prices["BNB"] = price
            except Exception as e:
                print(f"Error fetching BNB price: {str(e)}")

        return {
            "BNB": float(bnb_balance),
            "WBTC": float(wbtc_balance),
            "USDT": float(usdt_balance),
            "prices": usd_prices,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching balances: {str(e)}"
        ) 