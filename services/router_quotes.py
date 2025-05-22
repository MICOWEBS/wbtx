import asyncio
import logging
from typing import Dict, Optional, Tuple, List
import nest_asyncio
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from decimal import Decimal

from web3 import Web3, HTTPProvider
from config import env
from core.cache import ttl_cache
from core.constants import WBTC_ADDRESS, USDT_ADDRESS, WBNB_ADDRESS, PANCAKE_ROUTER_ADDRESS
from services.dex_price_feeds import get_price_from_dexscreener

# Apply nest_asyncio to allow nested event loops
nest_asyncio.apply()

# Setup logging
logger = logging.getLogger(__name__)

# BSC Mainnet addresses
USDT = Web3.to_checksum_address("0x55d398326f99059fF775485246999027B3197955")  # BSC USDT
WBTC = Web3.to_checksum_address("0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c")  # BSC WBTC
WBNB = Web3.to_checksum_address("0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c")  # BSC WBNB

# PancakeSwap v2 Router (Mainnet)
PANCAKE_ROUTER = Web3.to_checksum_address("0x10ED43C718714eb63d5aA57B78B54704E256024E")

# ERC20 ABI for decimals and other functions
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "name",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "totalSupply",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"}
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"}
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_value", "type": "uint256"}
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    }
]

# Token decimals (BSC mainnet values)
USDT_DECIMALS = 18  # BSC USDT uses 18 decimals
WBTC_DECIMALS = 18   # BSC WBTC uses 18 decimals
WBNB_DECIMALS = 18  # BSC WBNB uses 18 decimals

ROUTER_ABI = [
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
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
        ],
        "name": "getAmountsIn",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function",
    }
]

DEX_ROUTER_MAP = {
    "pancake": PANCAKE_ROUTER,
}

# Configure session with retries
session = requests.Session()
retries = Retry(
    total=5,
    backoff_factor=0.5,
    status_forcelist=[500, 502, 503, 504]
)
session.mount('http://', HTTPAdapter(max_retries=retries))
session.mount('https://', HTTPAdapter(max_retries=retries))

# List of BSC mainnet RPC endpoints to try
BSC_RPC_URLS = [
    "https://bsc-dataseed1.binance.org/",
    "https://bsc-dataseed2.binance.org/",
    "https://bsc-dataseed3.binance.org/",
    "https://bsc-dataseed4.binance.org/",
    "https://bsc-dataseed1.defibit.io/",
    "https://bsc-dataseed2.defibit.io/",
    "https://bsc-dataseed3.defibit.io/",
    "https://bsc-dataseed4.defibit.io/",
    "https://bsc-dataseed1.ninicoin.io/",
    "https://bsc-dataseed2.ninicoin.io/",
    "https://bsc-dataseed3.ninicoin.io/",
    "https://bsc-dataseed4.ninicoin.io/"
]

# Try each RPC endpoint until one works
w3 = None
for rpc_url in BSC_RPC_URLS:
    try:
        # Configure provider with timeout
        provider = HTTPProvider(
            rpc_url,
            request_kwargs={
                'timeout': 30
            }
        )
        w3 = Web3(provider)
        
        # Test connection with a simple call
        if w3.is_connected() and w3.eth.chain_id == 56:  # BSC mainnet chain ID
            logger.info(f"Connected to BSC Mainnet RPC: {rpc_url}")
            break
    except Exception as e:
        logger.warning(f"Failed to connect to {rpc_url}: {e}")
        continue

if not w3 or not w3.is_connected():
    raise Exception("Failed to connect to any BSC RPC endpoint")

# Initialize contracts with retry logic
def init_contract(address, abi, max_retries=3):
    for i in range(max_retries):
        try:
            contract = w3.eth.contract(address=address, abi=abi)
            # Test contract with a simple call
            if hasattr(contract.functions, 'decimals'):
                contract.functions.decimals().call({'from': '0x0000000000000000000000000000000000000000'})
            return contract
        except Exception as e:
            if i == max_retries - 1:
                raise
            logger.warning(f"Retry {i+1}/{max_retries} for contract {address}: {e}")
            continue

try:
    pancake_router_contract = init_contract(PANCAKE_ROUTER, ROUTER_ABI)
    usdt_contract = init_contract(USDT, ERC20_ABI)
    wbtc_contract = init_contract(WBTC, ERC20_ABI)
    wbnb_contract = init_contract(WBNB, ERC20_ABI)
except Exception as e:
    logger.error(f"Failed to initialize contracts: {e}")
    raise

# Get decimals from contracts
try:
    btcb_contract = w3.eth.contract(address=WBTC_ADDRESS, abi=ERC20_ABI)
    usdt_contract = w3.eth.contract(address=USDT_ADDRESS, abi=ERC20_ABI)
    wbnb_contract = w3.eth.contract(address=WBNB_ADDRESS, abi=ERC20_ABI)

    # Call decimals with the same parameters as trade_executor
    BTCB_DECIMALS = btcb_contract.functions.decimals().call({'from': '0x0000000000000000000000000000000000000000'})
    USDT_DECIMALS = usdt_contract.functions.decimals().call({'from': '0x0000000000000000000000000000000000000000'})
    WBNB_DECIMALS = wbnb_contract.functions.decimals().call({'from': '0x0000000000000000000000000000000000000000'})

    # Validate decimals
    if USDT_DECIMALS != 18:
        logger.warning(f"⚠️ USDT decimals mismatch: got {USDT_DECIMALS}, expected 18!")
        # Force to 18 decimals for BSC USDT
        USDT_DECIMALS = 18
        logger.info("Using 18 decimals for USDT (standard for BSC USDT)")
    
    if WBNB_DECIMALS != 18:
        logger.warning(f"⚠️ WBNB decimals mismatch: got {WBNB_DECIMALS}, expected 18!")
        # Force to 18 decimals for BSC WBNB
        WBNB_DECIMALS = 18
        logger.info("Using 18 decimals for WBNB (standard for BSC WBNB)")

    logger.info(f"Token decimals from contracts - USDT: {USDT_DECIMALS}, BTCB: {BTCB_DECIMALS}, WBNB: {WBNB_DECIMALS}")
    logger.info(f"BTCB contract address: {WBTC_ADDRESS}")
    logger.info(f"USDT contract address: {USDT_ADDRESS}")
    
    # Log the actual values we'll use
    logger.info(f"Using decimals - USDT: {USDT_DECIMALS}, BTCB: {BTCB_DECIMALS}, WBNB: {WBNB_DECIMALS}")
except Exception as e:
    logger.error(f"Error fetching token decimals: {e}")
    # Fallback to known values
    BTCB_DECIMALS = 18   # BSC BTCB uses 18 decimals
    USDT_DECIMALS = 18  # BSC USDT uses 18 decimals
    WBNB_DECIMALS = 18  # BSC WBNB uses 18 decimals
    logger.info(f"Using fallback decimals - USDT: {USDT_DECIMALS}, BTCB: {BTCB_DECIMALS}, WBNB: {WBNB_DECIMALS}")

# Define possible routes - try direct pair first, then WBNB path
ROUTES = [
    [USDT_ADDRESS, WBTC_ADDRESS],  # Direct USDT -> BTCB
    [USDT_ADDRESS, WBNB_ADDRESS, WBTC_ADDRESS],  # USDT -> WBNB -> BTCB
]

logger.info(f"Using BSC Mainnet RPC: {w3.provider.endpoint_uri}")


def to_token_amount(amount_float: float, decimals: int) -> int:
    """Convert float amount to token's smallest unit."""
    return int(amount_float * (10 ** decimals))


def from_token_amount(amount_int: int, decimals: int) -> float:
    """Convert token's smallest unit to float amount."""
    return amount_int / (10 ** decimals)


@ttl_cache(ttl=20)
async def get_amount_out(amount_in: int, dex: str) -> Optional[int]:
    """Get amount of BTCB for given USDT input trying multiple routes."""
    for route in ROUTES:
        try:
            logger.info(f"Trying route: {' -> '.join(route)}")
            logger.info(f"Amount in: {amount_in} (wei)")
            
            # Get the function object
            get_amounts_out = pancake_router_contract.functions.getAmountsOut(amount_in, route)
            
            # Call the function
            amounts = get_amounts_out.call()
            
            # Log the intermediate amounts for debugging
            logger.info(f"Route amounts: {amounts}")
            
            # The last amount is the BTCB output
            btcb_out = amounts[-1]
            
            # Convert to human readable for logging
            btcb_amount = from_token_amount(btcb_out, BTCB_DECIMALS)
            usdt_amount = from_token_amount(amount_in, USDT_DECIMALS)
            
            logger.info(f"Route successful - Input: {usdt_amount:.2f} USDT, Output: {btcb_amount:.8f} BTCB")
            return btcb_out
        except Exception as e:
            logger.error(f"Route failed: {' -> '.join(route)} - Error: {str(e)}")
            continue

    logger.error("All routes failed for get_amount_out")
    return None


@ttl_cache(ttl=20)
async def get_amount_in(amount_out: int, dex: str) -> Optional[int]:
    """Get amount of USDT needed for given BTCB output trying multiple routes."""
    for route in ROUTES:
        try:
            logger.info(f"Trying route: {' -> '.join(route)}")
            logger.info(f"Amount out: {amount_out} (wei)")
            
            # Get the function object
            get_amounts_in = pancake_router_contract.functions.getAmountsIn(amount_out, route)
            
            # Call the function
            amounts = get_amounts_in.call()
            
            # Log the intermediate amounts for debugging
            logger.info(f"Route amounts: {amounts}")
            
            # The first amount is the USDT input
            usdt_in = amounts[0]
            
            # Convert to human readable for logging
            usdt_amount = from_token_amount(usdt_in, USDT_DECIMALS)
            btcb_amount = from_token_amount(amount_out, BTCB_DECIMALS)
            
            logger.info(f"Route successful - Input: {usdt_amount:.2f} USDT, Output: {btcb_amount:.8f} BTCB")
            return usdt_in
        except Exception as e:
            logger.error(f"Route failed: {' -> '.join(route)} - Error: {str(e)}")
            continue

    logger.error("All routes failed for get_amount_in")
    return None


async def best_buy_quote(amount_usdt: float) -> Tuple[str, float]:
    """Get best buy price (USDT per BTCB) for given USDT amount."""
    try:
        # Convert to smallest unit (wei)
        amount_in_wei = to_token_amount(amount_usdt, USDT_DECIMALS)
        logger.info(f"Converting {amount_usdt} USDT to {amount_in_wei} wei")
        
        out = await get_amount_out(amount_in_wei, "pancake")
        if not out:
            return "pancake", 0.0

        # Convert BTCB output to base units (8 decimals)
        btcb_amount = from_token_amount(out, BTCB_DECIMALS)
        
        # Handle zero division
        if btcb_amount == 0:
            logger.warning("Zero BTCB amount received")
            return "pancake", 0.0

        # Calculate USDT per BTCB price (ensure float division)
        price = float(amount_usdt) / float(btcb_amount)  # This gives us USDT/BTCB

        logger.info(
            f"Debug Buy - USDT in: {amount_usdt}, "
            f"BTCB out: {btcb_amount:.8f}, "
            f"Price: {price:,.2f} USDT/BTCB (~${price:,.2f})"
        )
        return "pancake", price
    except Exception as e:
        logger.error(f"Error in best_buy_quote: {e}")
        return "pancake", 0.0


async def best_sell_quote(amount_btcb: float) -> Tuple[str, float]:
    """Get best sell price (USDT per BTCB) for given BTCB amount."""
    try:
        # Convert to smallest unit (8 decimals for BTCB)
        amount_out_wei = to_token_amount(amount_btcb, BTCB_DECIMALS)
        logger.info(f"Converting {amount_btcb} BTCB to {amount_out_wei} wei")
        
        in_amount = await get_amount_in(amount_out_wei, "pancake")
        if not in_amount:
            return "pancake", 0.0

        # Convert USDT input to base units (18 decimals)
        usdt_amount = from_token_amount(in_amount, USDT_DECIMALS)
        
        # Handle zero division
        if amount_btcb == 0:
            logger.warning("Zero BTCB amount input")
            return "pancake", 0.0

        # Calculate USDT per BTCB price (ensure float division)
        price = float(usdt_amount) / float(amount_btcb)  # This gives us USDT/BTCB

        logger.info(
            f"Debug Sell - BTCB in: {amount_btcb}, "
            f"USDT out: {usdt_amount:.2f}, "
            f"Price: {price:,.2f} USDT/BTCB (~${price:,.2f})"
        )
        return "pancake", price
    except Exception as e:
        logger.error(f"Error in best_sell_quote: {e}")
        return "pancake", 0.0 