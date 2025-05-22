from web3 import Web3

# BSC Mainnet addresses
WBTC_ADDRESS = Web3.to_checksum_address("0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c")  # BSC BTCB (Bitcoin BEP20)
USDT_ADDRESS = Web3.to_checksum_address("0x55d398326f99059fF775485246999027B3197955")  # BSC USDT
WBNB_ADDRESS = Web3.to_checksum_address("0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c")  # BSC WBNB

# PancakeSwap v2 Router (Mainnet)
PANCAKE_ROUTER_ADDRESS = Web3.to_checksum_address("0x10ED43C718714eb63d5aA57B78B54704E256024E")

ROUTER_BY_DEX = {
    "pancake": PANCAKE_ROUTER_ADDRESS,
} 