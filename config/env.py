import os
from dotenv import load_dotenv
from urllib.parse import urlparse

# Load environment variables from .env file
load_dotenv()

# Parse PostgreSQL URL
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://wbtcbnb_c9qi_user:O7eMn14xO3vagxbTBkVWJjaNsZV0uuF8@dpg-d0ldthffte5s739e0tr0-a.frankfurt-postgres.render.com/wbtcbnb_c9qi")
if POSTGRES_URL:
    parsed = urlparse(POSTGRES_URL)
    DB_USER = parsed.username
    DB_PASSWORD = parsed.password
    DB_HOST = parsed.hostname
    DB_PORT = parsed.port or 5432
    DB_NAME = parsed.path.lstrip('/')
else:
    # Fallback to individual environment variables
    DB_HOST = os.getenv('DB_HOST', 'localhost')
    DB_PORT = int(os.getenv('DB_PORT', '5432'))
    DB_USER = os.getenv('DB_USER', 'postgres')
    DB_PASSWORD = os.getenv('DB_PASSWORD', 'postgres')
    DB_NAME = os.getenv('DB_NAME', 'bgf')

# JWT configuration
SECRET_KEY = os.getenv('SECRET_KEY', 'your-secret-key-here')  # Change this in production
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Other configuration
MAX_DAILY_LOSS_PERCENT = float(os.getenv('MAX_DAILY_LOSS_PERCENT', '5.0'))
COOL_OFF_CONSEC_LOSSES = int(os.getenv('COOL_OFF_CONSEC_LOSSES', '3'))
SIGNAL_INTERVAL = int(os.getenv('SIGNAL_INTERVAL', '60'))

BSC_RPC_URL = os.getenv("BSC_RPC_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
TAAPI_SECRET = os.getenv("TAAPI_SECRET")

SIGNAL_INTERVAL = int(os.getenv("SIGNAL_INTERVAL", 60))
MAX_TRADE_PERCENT = float(os.getenv("MAX_TRADE_PERCENT", 10))
SLIPPAGE_TOLERANCE = float(os.getenv("SLIPPAGE_TOLERANCE", 0.2))
TRAILING_STOP_PERCENT = float(os.getenv("TRAILING_STOP_PERCENT", 0.5))

MAX_GAS_PRICE_GWEI = float(os.getenv("MAX_GAS_PRICE_GWEI", 5))
MAX_GAS_FEE_BNB = float(os.getenv("MAX_GAS_FEE_BNB", 0.003))

BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")
HARD_STOP_LOSS_PERCENT = float(os.getenv("HARD_STOP_LOSS_PERCENT", 2))
MAX_DAILY_LOSS_PERCENT = float(os.getenv("MAX_DAILY_LOSS_PERCENT", 5))

MIN_TRADE_PERCENT = float(os.getenv("MIN_TRADE_PERCENT", 2))
COOL_OFF_CONSEC_LOSSES = int(os.getenv("COOL_OFF_CONSEC_LOSSES", 3))

# Tx bumping
TX_TIMEOUT_SEC = int(os.getenv("TX_TIMEOUT_SEC", 120))
GAS_BUMP_FACTOR = float(os.getenv("GAS_BUMP_FACTOR", 1.2))
MAX_BUMPS = int(os.getenv("MAX_BUMPS", 3))

MAC_D_FILTER = os.getenv("MACD_FILTER", "true").lower() == "true"

REQUIRED_VARS = [
    "BSC_RPC_URL",
    "PRIVATE_KEY",
    "TAAPI_SECRET",
    "POSTGRES_URL",
]

def validate_env():
    """Validate that all required environment variables are set"""
    missing = [var for var in REQUIRED_VARS if not os.getenv(var)]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
