import asyncio
from typing import Optional
import os
from time import time
import csv, io
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, Response, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from starlette.websockets import WebSocketDisconnect

from core.logger import get_logger
from core import strategy, risk, auth
from core.trade_executor import TradeExecutor
from db.models import Database, log_signal
from config import env
from core.metrics import API_LATENCY_SECONDS, metrics_text

logger = get_logger(__name__)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# Middleware to record latency
class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = time()
        response = await call_next(request)
        elapsed = time() - start
        API_LATENCY_SECONDS.labels(request.url.path).observe(elapsed)
        return response

app.add_middleware(MetricsMiddleware)

# Serve built frontend if present
frontend_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

async def get_current_user(token: str = Depends(oauth2_scheme)):
    from jose import jwt, JWTError
    try:
        payload = jwt.decode(token, auth.SECRET_KEY, algorithms=[auth.ALGORITHM])
        username = payload.get("sub")
        if username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        user = await Database.fetch_one("users", {"username": username})
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return username
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

class BotRunner:
    _task: Optional[asyncio.Task] = None
    _running: bool = False
    executor: TradeExecutor = TradeExecutor()
    _last_signal_time: float = 0
    _last_status_log: float = 0  # Track last status log time

    @classmethod
    async def _loop(cls):
        logger.info("Bot loop started")
        while cls._running:
            try:
                # daily drawdown guard
                daily_pl = await Database.daily_profit_usd()
                if daily_pl <= -abs(env.MAX_DAILY_LOSS_PERCENT):
                    logger.warning("Daily loss limit reached. Stopping bot.")
                    cls._running = False
                    break

                # consecutive loss guard
                losses = await risk.consecutive_losses()
                if losses >= env.COOL_OFF_CONSEC_LOSSES:
                    logger.warning("Consecutive loss limit reached. Cooling off.")
                    cls._running = False
                    break

                signal = await strategy.evaluate()
                if signal:
                    cls._last_signal_time = time()
                    logger.info(f"Signal received: {signal['action']} at {signal['dex_price']} (spread: {signal['price_spread']:.2f}%)")
                    await log_signal(signal["action"], signal["rsi"], signal["ema"], signal["price_spread"])
                    # recreate executor if dex differs
                    if getattr(cls.executor, 'dex_source', 'pancake') != signal.get('dex_source', 'pancake'):
                        cls.executor = TradeExecutor(signal.get('dex_source', 'pancake'))
                        cls.executor.dex_source = signal.get('dex_source', 'pancake')

                    # dynamic pos size
                    pct = await risk.dynamic_position_pct()
                    signal["position_pct"] = pct
                    # attempt with fallback dex list
                    options = signal.get('dex_options', [signal.get('dex_source', 'pancake')])
                    for dex in options:
                        try:
                            if getattr(cls.executor, 'dex_source', 'pancake') != dex:
                                cls.executor = TradeExecutor(dex)
                                cls.executor.dex_source = dex
                            await cls.executor.execute(signal)
                            break
                        except Exception as exc:
                            if 'INSUFFICIENT_LIQUIDITY' in str(exc):
                                logger.warning(f"Router {dex} liquidity issue, trying next")
                                continue
                            raise
            except Exception as exc:
                logger.exception(exc)
            await asyncio.sleep(env.SIGNAL_INTERVAL)
        logger.info("Bot loop stopped")

    @classmethod
    async def start(cls):
        if not cls._running:
            cls._running = True
            cls._task = asyncio.create_task(cls._loop())
            logger.info("Bot started successfully")
            return True
        logger.info("Bot already running")
        return False

    @classmethod
    async def stop(cls):
        if cls._running:
            cls._running = False
            if cls._task:
                await cls._task
                logger.info("Bot stopped successfully")
            else:
                logger.info("Bot already stopped")

    @classmethod
    def status(cls):
        status = "running" if cls._running else "stopped"
        if cls._running:
            current_time = time()
            time_since_signal = current_time - cls._last_signal_time if cls._last_signal_time > 0 else float('inf')
            time_since_last_log = current_time - cls._last_status_log
            
            # Only log status every 30 seconds
            if time_since_last_log >= 30:
                if time_since_signal > env.SIGNAL_INTERVAL * 2:
                    logger.info(f"Bot is running but no signals received in {time_since_signal:.1f} seconds")
                cls._last_status_log = current_time
                
        return status

    @classmethod
    async def stop_trading(cls):
        """Stop the trading bot"""
        if cls._running:
            cls._running = False
            logger.info("Trading bot stopped")
            return {"status": "success", "message": "Trading bot stopped"}
        return {"status": "error", "message": "Trading bot is not running"}


@app.on_event("startup")
async def startup_event():
    env.validate_env()
    await Database.init()
    import core.tx_bumper as tx_bumper
    asyncio.create_task(tx_bumper.bump_loop())
    import core.tp_watcher as tp_watcher
    asyncio.create_task(tp_watcher.tp_loop())


@app.post("/bot/start", dependencies=[Depends(get_current_user)])
async def start_bot():
    if await BotRunner.start():
        return {"status": "started"}
    return {"status": "already_running"}


@app.post("/bot/stop", dependencies=[Depends(get_current_user)])
async def stop_bot():
    await BotRunner.stop()
    return {"status": "stopped"}


@app.get("/bot/status")
async def bot_status():
    return {"status": BotRunner.status()}


# Simple websocket broadcast for clients to receive heartbeat
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    try:
        await ws.accept()
        while True:
            try:
                await ws.send_json({"status": BotRunner.status()})
                await asyncio.sleep(1)
            except WebSocketDisconnect:
                logger.info("Client disconnected")
                break
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                # Don't break on other errors, try to continue
                await asyncio.sleep(1)
    except Exception as e:
        logger.error(f"WebSocket connection error: {e}")
    finally:
        try:
            await ws.close()
        except:
            pass


# ----- Data Feeds for Frontend ----- #


@app.get("/signals", dependencies=[Depends(get_current_user)])
async def get_signals(page: int = 1, page_size: int = 20, type: Optional[str] = None):
    from db.models import Database

    filters = {"type": type} if type else None
    offset = (page - 1) * page_size
    rows = await Database.fetch_recent("signals", limit=page_size, offset=offset, filters=filters)
    return rows


@app.get("/trades", dependencies=[Depends(get_current_user)])
async def get_trades(page: int = 1, page_size: int = 20, trade_type: Optional[str] = None):
    from db.models import Database

    filters = {"trade_type": trade_type} if trade_type else None
    offset = (page - 1) * page_size
    rows = await Database.fetch_recent("trades", limit=page_size, offset=offset, filters=filters)
    return rows


@app.get("/stats", dependencies=[Depends(get_current_user)])
async def get_stats():
    from db.models import Database

    total_profit = await Database.total_profit()
    total_profit_usd = await Database.total_profit_usd()
    win_rate, avg_trade = await Database.win_rate_avg()
    return {
        "total_profit": total_profit,
        "total_profit_usd": total_profit_usd,
        "win_rate": win_rate,
        "average_trade_usd": avg_trade,
    }


@app.get("/stats/daily", dependencies=[Depends(get_current_user)])
async def get_stats_daily():
    from db.models import Database

    if Database._pool is None:
        await Database.init()

    query = "SELECT DATE(timestamp) AS day, SUM(profit_usd) AS profit_usd FROM trades GROUP BY day ORDER BY day;"
    async with Database._pool.acquire() as conn:
        rows = await conn.fetch(query)
        return [dict(r) for r in rows]


@app.get("/stats/equity", dependencies=[Depends(get_current_user)])
async def stats_equity():
    from db.models import Database
    curve = await Database.equity_curve()
    return curve


# ---- Cache utilities ----


@app.post("/cache/clear", dependencies=[Depends(get_current_user)])
async def clear_api_cache():
    from core.cache import clear_cache

    await clear_cache()
    return {"status": "cleared"}


# Prometheus metrics endpoint


@app.get("/metrics")
async def metrics_route():
    return Response(metrics_text(), media_type="text/plain; version=0.0.4")


# ---- wallet balances ----
@app.get("/wallet/balances", dependencies=[Depends(get_current_user)])
async def wallet_balances(address: Optional[str] = None):
    try:
        from services.wallet_balances import get_balances
        addr = address or TradeExecutor().account.address  # default bot wallet
        return await get_balances(addr)
    except Exception as e:
        logger.error(f"Error fetching wallet balances: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


# CSV export helpers


def rows_to_csv(rows):
    if not rows:
        return ""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


@app.get("/signals/csv", dependencies=[Depends(get_current_user)])
async def export_signals_csv(type: Optional[str] = None):
    from db.models import Database

    filters = {"type": type} if type else None
    rows = await Database.fetch_recent("signals", limit=10000, offset=0, filters=filters)
    csv_text = rows_to_csv(rows)
    return Response(csv_text, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=signals.csv"})


@app.get("/trades/csv", dependencies=[Depends(get_current_user)])
async def export_trades_csv(trade_type: Optional[str] = None):
    from db.models import Database

    filters = {"trade_type": trade_type} if trade_type else None
    rows = await Database.fetch_recent("trades", limit=10000, offset=0, filters=filters)
    csv_text = rows_to_csv(rows)
    return Response(csv_text, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=trades.csv"})

@app.post("/auth/register")
async def register(form: OAuth2PasswordRequestForm = Depends()):
    hashed = auth.hash_password(form.password)
    user_data = {
        "id": __import__('uuid').uuid4(),
        "username": form.username,
        "password_hash": hashed
    }
    # created_at will be set by the database default
    await Database.insert("users", user_data)
    return {"status": "created"}


@app.post("/auth/login")
async def login(form: OAuth2PasswordRequestForm = Depends()):
    try:
        user = await auth.authenticate_user(form.username, form.password)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = auth.create_access_token({"sub": form.username})
        return {"access_token": token, "token_type": "bearer"}
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )