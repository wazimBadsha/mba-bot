import asyncio
import os
import sqlite3
import pytest
from datetime import datetime
from main import ScalpingBot

# Dummy exchange to simulate required methods
class DummyExchange:
    async def fetch_ohlcv(self, symbol, timeframe, limit=50):
        # return dummy OHLCV data
        return [[datetime.utcnow().timestamp(), 2650, 2660, 2640, 2655, 1000]] * limit
    async def fetch_ticker(self, symbol):
        return {"last": "2655"}
    async def create_order(self, symbol, order_type, side, quantity):
        return {"id": "order123", "status": "FILLED"}
    async def cancel_order(self, order_id, symbol):
        return {"status": "cancelled"}
    async def fetch_order(self, order_id, symbol):
        return {"status": "FILLED"}
    async def watch_ohlcv(self, symbol, timeframe):
        async def dummy():
            await asyncio.sleep(0.01)
            return (None, [[datetime.utcnow().timestamp(), 2650, 2660, 2640, 2655, 1000]])
        return dummy()
    async def watch_order_book(self, symbol):
        async def dummy():
            await asyncio.sleep(0.01)
            return (None, {"bids": [[2650, 100]]*5, "asks": [[2655, 160]]*5})
        return dummy()
    async def watch_trades(self, symbol):
        async def dummy():
            await asyncio.sleep(0.01)
            return (None, [[datetime.utcnow().timestamp(), 2655, 10, "sell"]])
        return dummy()
    async def close(self):
        pass

@pytest.fixture
def bot_instance(tmp_path):
    os.environ["BINANCE_API_KEY"] = "dummy"
    os.environ["BINANCE_SECRET_KEY"] = "dummy"
    bot = ScalpingBot("dummy", "dummy")
    bot.exchange = DummyExchange()
    yield bot
    asyncio.run(bot.close())

@pytest.mark.asyncio
async def test_order_history_insertion(bot_instance):
    bot_instance.log_order_history("ETH/USDT", "short", 2650, 2645, -0.02)
    cur = bot_instance.db_conn.cursor()
    cur.execute("SELECT * FROM orders")
    rows = cur.fetchall()
    assert len(rows) >= 1

@pytest.mark.asyncio
async def test_log_db(bot_instance):
    bot_instance.log_to_db("Test log message")
    cur = bot_instance.db_conn.cursor()
    cur.execute("SELECT * FROM logs")
    rows = cur.fetchall()
    assert any("Test log message" in row for row in rows)

@pytest.mark.asyncio
async def test_e2e_flow(bot_instance):
    # simulate signal generation and trade execution
    task = asyncio.create_task(bot_instance.trade_id_engine.run())
    await asyncio.sleep(0.05)  # allow dummy task to complete
    result = await task
    assert result is not None
