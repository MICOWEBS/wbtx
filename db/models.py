import uuid
from typing import Any, Dict, Optional

import asyncpg

from config import env
from core.logger import get_logger
from core.metrics import increment_signal, increment_trade, increment_error, set_total_profit_usd

logger = get_logger(__name__)


class Database:
    _pool: Optional[asyncpg.pool.Pool] = None

    @classmethod
    async def init(cls):
        if cls._pool is None:
            cls._pool = await asyncpg.create_pool(
                host=env.DB_HOST,
                port=env.DB_PORT,
                user=env.DB_USER,
                password=env.DB_PASSWORD,
                database=env.DB_NAME
            )
            logger.info("PostgreSQL pool created")
            await cls.run_migrations()
            await cls._create_tables()

    @classmethod
    async def run_migrations(cls):
        """Run database migrations"""
        if cls._pool is None:
            await cls.init()

        # Create migrations table if it doesn't exist
        async with cls._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS migrations (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL UNIQUE,
                    applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Get list of applied migrations
            applied = await conn.fetch("SELECT name FROM migrations")
            applied = {r['name'] for r in applied}

            # Get list of migration files
            import os
            import glob
            migration_files = sorted(glob.glob('backend/db/migrations/*.sql'))

            # Apply new migrations
            for migration_file in migration_files:
                migration_name = os.path.basename(migration_file)
                if migration_name not in applied:
                    print(f"Applying migration: {migration_name}")
                    with open(migration_file, 'r') as f:
                        sql = f.read()
                        await conn.execute(sql)
                        await conn.execute(
                            "INSERT INTO migrations (name) VALUES ($1)",
                            migration_name
                        )
                    print(f"Applied migration: {migration_name}")

    @classmethod
    async def _create_tables(cls):
        async with cls._pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id UUID PRIMARY KEY,
                    type TEXT,
                    rsi FLOAT,
                    ema FLOAT,
                    price_spread FLOAT,
                    timestamp TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS trades (
                    id UUID PRIMARY KEY,
                    trade_type TEXT,
                    amount FLOAT,
                    entry_price FLOAT,
                    exit_price FLOAT,
                    profit FLOAT,
                    profit_usd FLOAT DEFAULT 0,
                    expected_out FLOAT DEFAULT 0,
                    tx_hash TEXT,
                    timestamp TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS errors (
                    id UUID PRIMARY KEY,
                    context TEXT,
                    message TEXT,
                    timestamp TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS users (
                    id UUID PRIMARY KEY,
                    username TEXT UNIQUE,
                    password_hash TEXT
                );

                CREATE TABLE IF NOT EXISTS pending_txs (
                    tx_hash TEXT PRIMARY KEY,
                    nonce BIGINT,
                    gas_price BIGINT,
                    sent_at TIMESTAMP DEFAULT NOW(),
                    bumps INT DEFAULT 0,
                    status TEXT DEFAULT 'pending'
                );

                CREATE TABLE IF NOT EXISTS positions (
                    id UUID PRIMARY KEY,
                    side TEXT,
                    entry_price FLOAT,
                    qty_total FLOAT,
                    qty_left FLOAT,
                    tp1_hit BOOLEAN DEFAULT FALSE,
                    tp2_hit BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                """
            )
            logger.info("Tables ensured")

    @classmethod
    async def insert(cls, table: str, data: dict):
        """Insert a row into a table"""
        if cls._pool is None:
            await cls.init()

        columns = list(data.keys())
        values = list(data.values())
        placeholders = [f"${i+1}" for i in range(len(values))]
        
        query = f"""
            INSERT INTO {table} ({', '.join(columns)})
            VALUES ({', '.join(placeholders)})
        """

        async with cls._pool.acquire() as conn:
            await conn.execute(query, *values)

    @classmethod
    async def fetch_one(cls, table: str, filters: dict = None):
        """Fetch a single row from a table with optional filters"""
        if cls._pool is None:
            await cls.init()

        query = f"SELECT * FROM {table}"
        values = []
        if filters:
            conditions = []
            for key, value in filters.items():
                conditions.append(f"{key} = ${len(values) + 1}")
                values.append(value)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
        query += " LIMIT 1"

        async with cls._pool.acquire() as conn:
            row = await conn.fetchrow(query, *values)
            return dict(row) if row else None

    @classmethod
    async def fetch_recent(cls, table: str, limit: int = 20, offset: int = 0, filters: dict = None):
        """Fetch recent rows from a table with optional filters"""
        if cls._pool is None:
            await cls.init()

        query = f"SELECT * FROM {table}"
        values = []
        if filters:
            conditions = []
            for key, value in filters.items():
                conditions.append(f"{key} = ${len(values) + 1}")
                values.append(value)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)

        # Only add ORDER BY if the table has a timestamp column
        if table in ['signals', 'trades']:  # Add other tables that have timestamp
            query += " ORDER BY timestamp DESC"
        
        query += f" LIMIT {limit} OFFSET {offset}"

        async with cls._pool.acquire() as conn:
            rows = await conn.fetch(query, *values)
            return [dict(r) for r in rows]

    @classmethod
    async def total_profit(cls) -> float:
        if cls._pool is None:
            raise RuntimeError("Database pool not initialized")
        query = "SELECT COALESCE(SUM(profit), 0) AS total FROM trades;"
        async with cls._pool.acquire() as conn:
            row = await conn.fetchrow(query)
            return row["total"] if row else 0.0

    @classmethod
    async def total_profit_usd(cls) -> float:
        if cls._pool is None:
            raise RuntimeError("Database pool not initialized")
        query = "SELECT COALESCE(SUM(profit_usd), 0) AS total FROM trades;"
        async with cls._pool.acquire() as conn:
            row = await conn.fetchrow(query)
            return row["total"] if row else 0.0

    @classmethod
    async def daily_profit_usd(cls) -> float:
        if cls._pool is None:
            raise RuntimeError("Database pool not initialized")
        query = "SELECT COALESCE(SUM(profit_usd), 0) AS total FROM trades WHERE DATE(timestamp) = CURRENT_DATE;"
        async with cls._pool.acquire() as conn:
            row = await conn.fetchrow(query)
            return row["total"] if row else 0.0

    @classmethod
    async def equity_curve(cls):
        if cls._pool is None:
            raise RuntimeError("Database pool not initialized")
        query = "SELECT timestamp, profit_usd FROM trades ORDER BY timestamp ASC;"
        async with cls._pool.acquire() as conn:
            rows = await conn.fetch(query)
        curve = []
        cum = 0.0
        for r in rows:
            cum += r["profit_usd"]
            curve.append({"timestamp": r["timestamp"], "equity": cum})
        return curve

    @classmethod
    async def win_rate_avg(cls):
        if cls._pool is None:
            raise RuntimeError("Database pool not initialized")
        query = "SELECT profit_usd FROM trades;"
        async with cls._pool.acquire() as conn:
            rows = await conn.fetch(query)
        if not rows:
            return 0.0, 0.0
        wins = [r["profit_usd"] for r in rows if r["profit_usd"] > 0]
        win_rate = len(wins) / len(rows) * 100
        avg = sum([r["profit_usd"] for r in rows]) / len(rows)
        return win_rate, avg

    @classmethod
    async def pending_rows(cls):
        if cls._pool is None:
            return []
        query = "SELECT * FROM pending_txs WHERE status='pending';"
        async with cls._pool.acquire() as conn:
            rows = await conn.fetch(query)
            return [dict(r) for r in rows]

    @classmethod
    async def mark_mined(cls, tx_hash: str):
        if cls._pool is None:
            return
        query = "UPDATE pending_txs SET status='mined' WHERE tx_hash=$1;"
        async with cls._pool.acquire() as conn:
            await conn.execute(query, tx_hash)

    @classmethod
    async def update_bump(cls, tx_hash: str, new_hash: str, gas_price: int, bumps: int):
        if cls._pool is None:
            return
        query = "UPDATE pending_txs SET tx_hash=$1, gas_price=$2, bumps=$3, sent_at=NOW() WHERE tx_hash=$4;"
        async with cls._pool.acquire() as conn:
            await conn.execute(query, new_hash, gas_price, bumps, tx_hash)


async def log_signal(signal_type: str, rsi: float, ema: float, price_spread: float):
    await Database.insert(
        "signals",
        {
            "id": uuid.uuid4(),
            "type": signal_type,
            "rsi": rsi,
            "ema": ema,
            "price_spread": price_spread,
        },
    )
    increment_signal(signal_type)


async def log_trade(trade_type: str, amount: float, entry_price: float, exit_price: float, profit: float, tx_hash: str, profit_usd: float = 0.0, expected_out: float = 0.0):
    await Database.insert(
        "trades",
        {
            "id": uuid.uuid4(),
            "trade_type": trade_type,
            "amount": amount,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "profit": profit,
            "profit_usd": profit_usd,
            "expected_out": expected_out,
            "tx_hash": tx_hash,
        },
    )
    increment_trade(trade_type)
    total = await Database.total_profit_usd()
    set_total_profit_usd(total)


async def log_error(context: str, message: str):
    await Database.insert(
        "errors",
        {
            "id": uuid.uuid4(),
            "context": context,
            "message": message,
        },
    )
    increment_error(context)


async def create_user(username: str, pw_hash: str):
    await Database.insert("users", {"id": uuid.uuid4(), "username": username, "password_hash": pw_hash})


async def get_user(username: str):
    if Database._pool is None:
        raise RuntimeError("DB not init")
    query = "SELECT * FROM users WHERE username=$1;"
    async with Database._pool.acquire() as conn:
        return await conn.fetchrow(query, username)


async def insert_pending(tx_hash: str, nonce: int, gas_price: int):
    await Database.insert("pending_txs", {"tx_hash": tx_hash, "nonce": nonce, "gas_price": gas_price})


async def insert_position(side: str, entry_price: float, qty: float):
    await Database.insert(
        "positions",
        {
            "id": uuid.uuid4(),
            "side": side,
            "entry_price": entry_price,
            "qty_total": qty,
            "qty_left": qty,
            "tp1_hit": False,
            "tp2_hit": False,
        },
    )


async def list_positions():
    if Database._pool is None:
        return []
    query = "SELECT * FROM positions;"
    async with Database._pool.acquire() as conn:
        rows = await conn.fetch(query)
        return [dict(r) for r in rows]


async def update_position_qty(pos_id, qty_left: float, tp1_hit: bool, tp2_hit: bool):
    if Database._pool is None:
        return
    query = "UPDATE positions SET qty_left=$1, tp1_hit=$2, tp2_hit=$3 WHERE id=$4;"
    async with Database._pool.acquire() as conn:
        await conn.execute(query, qty_left, tp1_hit, tp2_hit, pos_id)


async def delete_position(pos_id):
    if Database._pool is None:
        return
    query = "DELETE FROM positions WHERE id=$1;"
    async with Database._pool.acquire() as conn:
        await conn.execute(query, pos_id) 


# --- Module-level wrappers for tx_bumper compatibility ---
async def pending_rows():
    return await Database.pending_rows()

async def mark_mined(tx_hash: str):
    await Database.mark_mined(tx_hash)

async def update_bump(tx_hash: str, new_hash: str, gas_price: int, bumps: int):
    await Database.update_bump(tx_hash, new_hash, gas_price, bumps) 