from prometheus_client import Counter, Gauge, Histogram, generate_latest

# Counters
SIGNALS_TOTAL = Counter('signals_total', 'Total signals generated', ['type'])
TRADES_TOTAL = Counter('trades_total', 'Total trades executed', ['trade_type'])
ERRORS_TOTAL = Counter('errors_total', 'Total errors', ['context'])

# Gauges
TOTAL_PROFIT_USD = Gauge('total_profit_usd', 'Cumulative profit in USD')

# Histograms
API_LATENCY_SECONDS = Histogram('api_latency_seconds', 'API call latency', ['endpoint'])

def increment_signal(signal_type: str):
    SIGNALS_TOTAL.labels(signal_type).inc()

def increment_trade(trade_type: str):
    TRADES_TOTAL.labels(trade_type).inc()

def increment_error(context: str):
    ERRORS_TOTAL.labels(context).inc()

def set_total_profit_usd(value: float):
    TOTAL_PROFIT_USD.set(value)

def metrics_text() -> bytes:
    return generate_latest() 