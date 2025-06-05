import asyncio
import os
import sqlite3
from datetime import datetime, UTC

from ccxt.async_support import binance
from trade_identification import TradeIdentificationEngine
from strategy_core import MathematicalStrategyCore
from risk_execution import RiskExecutionLayer
from dotenv import load_dotenv

load_dotenv()  # load environment variables


class ScalpingBot:
    def __init__(self, api_key, api_secret):
        self.exchange = binance(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "options": {"defaultType": "future"},
            }
        )
        self.symbol = os.getenv("SYMBOL", "ETH/USDT")
        # Remove leverage setting from constructor!
        self.trade_id_engine = TradeIdentificationEngine(self.exchange, symbol=self.symbol)
        self.strategy_core = MathematicalStrategyCore()
        self.risk_exec = RiskExecutionLayer(self.exchange, symbol=self.symbol)
        self.journal = open("trade_journal.log", "a")
        self.db_conn = sqlite3.connect("trading_bot.db")
        self.create_tables()
        self.current_trade = None  # {"status": "open"/"stopped"/None, ...}

    def create_tables(self):
        cur = self.db_conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                side TEXT,
                entry_price REAL,
                exit_price REAL,
                pnl REAL
            )
        """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                message TEXT
            )
        """
        )
        self.db_conn.commit()

    def log_order_history(self, symbol, side, entry_price, exit_price, pnl):
        cur = self.db_conn.cursor()
        cur.execute(
            """
            INSERT INTO orders (timestamp, symbol, side, entry_price, exit_price, pnl)
            VALUES (?,?,?,?,?,?)
        """,
            (datetime.now(UTC).isoformat(), symbol, side, entry_price, exit_price, pnl),
        )
        self.db_conn.commit()

    def log_to_db(self, message):
        cur = self.db_conn.cursor()
        cur.execute(
            """
            INSERT INTO logs (timestamp, message)
            VALUES (?,?)
        """,
            (datetime.now(UTC).isoformat(), message),
        )
        self.db_conn.commit()

    async def on_new_1m_bar(self, bar):
        close = bar[4]
        self.strategy_core.update_1m_close(close)

    async def monitor_open_trade(self):
        # Poll ATR & mark price every 5s to check for ATR-based exit
        while self.current_trade and self.current_trade["status"] == "open":
            bars_1m = await self.exchange.fetch_ohlcv(self.symbol, "1m", limit=50)
            highs = [b[2] for b in bars_1m]
            lows = [b[3] for b in bars_1m]
            closes = [b[4] for b in bars_1m]
            atr = self.strategy_core.compute_atr(highs, lows, closes, window=14)
            ticker = await self.exchange.fetch_ticker(self.symbol)
            mark_price = float(ticker["last"])

            new_stop = self.strategy_core.adaptive_stop_loss(
                self.current_trade["price"], mark_price, atr
            )
            if new_stop == "exit_now":
                side = "BUY" if self.current_trade["signal"] == "short" else "SELL"
                await self.exchange.create_order(
                    self.symbol, "MARKET", side, self.current_trade["quantity"]
                )
                pnl = (
                    (mark_price - self.current_trade["price"]) / self.current_trade["price"]
                    if self.current_trade["signal"] == "long"
                    else (self.current_trade["price"] - mark_price) / self.current_trade["price"]
                )
                self.strategy_core.update_trade_return(pnl * 100)
                log_msg = f"{datetime.now(UTC)} EXIT_AT_MARKET {side} price={mark_price} pnl={pnl}"
                self.journal.write(log_msg + "\n")
                self.log_to_db(log_msg)
                self.log_order_history(
                    self.symbol, self.current_trade["signal"], self.current_trade["price"], mark_price, pnl
                )
                self.current_trade = None
                break

            await asyncio.sleep(5)

    async def set_leverage_safe(self, leverage=1):
        try:
            await self.exchange.set_leverage(leverage, self.symbol)
            msg = f"{datetime.now(UTC)} Set leverage to {leverage} for {self.symbol}"
            self.journal.write(msg + "\n")
            self.log_to_db(msg)
        except Exception as e:
            msg = f"{datetime.now(UTC)} WARNING: Could not set leverage to {leverage} for {self.symbol}: {e}"
            self.journal.write(msg + "\n")
            self.log_to_db(msg)

    async def main_loop(self):
        await self.set_leverage_safe(1)
        await self.trade_id_engine.fetch_historical()
        bar_list = await self.exchange.fetch_ohlcv(self.symbol, "1m", limit=50)
        for bar in bar_list:
            await self.trade_id_engine.on_new_1m_bar(bar)
            self.strategy_core.update_1m_close(bar[4])

        identification_task = asyncio.create_task(self.trade_id_engine.run())
        while True:
            result = await identification_task
            if result and result["signal"] in ("short", "long"):
                entry_price = result["price"]

                # Strict money management and liquidation safety check before trade
                try:
                    balance = await self.exchange.fetch_balance()
                    usdt_balance = balance.get("total", {}).get("USDT", 10000)
                except Exception:
                    usdt_balance = 10000

                kelly_fraction = self.risk_exec.estimate_kelly_fraction()
                max_risk_usd = getattr(self.risk_exec, "max_risk", 100)
                position_size_usd = min(usdt_balance * kelly_fraction, max_risk_usd)

                # Liquidation safety: never risk more than max_risk_usd, never trade if not enough balance
                if position_size_usd > max_risk_usd or usdt_balance < max_risk_usd or position_size_usd <= 0:
                    msg = f"{datetime.now(UTC)} BLOCKED_MONEY_MANAGEMENT_OR_LIQUIDATION: position_size_usd={position_size_usd:.2f}, usdt_balance={usdt_balance:.2f}, max_risk_usd={max_risk_usd}"
                    self.journal.write(msg + "\n")
                    self.log_to_db(msg)
                    identification_task = asyncio.create_task(self.trade_id_engine.run())
                    await asyncio.sleep(0.01)
                    continue

                if not self.strategy_core.allow_new_trades():
                    msg = f"{datetime.now(UTC)} BLOCKED_SHARPE_CRITERIA"
                    self.journal.write(msg + "\n")
                    self.log_to_db(msg)
                    identification_task = asyncio.create_task(self.trade_id_engine.run())
                    continue

                trade_resp = await self.risk_exec.execute_trade(result, entry_price)
                if trade_resp and trade_resp["status"] == "open":
                    self.current_trade = {
                        "price": entry_price,
                        "quantity": trade_resp["quantity"],
                        "signal": result["signal"],
                        "status": "open",
                    }
                    msg = f"{datetime.now(UTC)} PLAN_{result['signal'].upper()} price={entry_price} qty={trade_resp['quantity']}"
                    self.journal.write(msg + "\n")
                    self.log_to_db(msg)
                    asyncio.create_task(self.monitor_open_trade())
                else:
                    if trade_resp:
                        msg = f"{datetime.now(UTC)} MICRO_STOP price={trade_resp['price']} pnl={trade_resp['pnl']}"
                        self.journal.write(msg + "\n")
                        self.log_to_db(msg)
                identification_task = asyncio.create_task(self.trade_id_engine.run())

            await asyncio.sleep(0.01)

    async def close(self):
        await self.exchange.close()
        self.journal.close()
        self.db_conn.close()


if __name__ == "__main__":
    # Load config from .env file
    load_dotenv()  # Load environment variables
    API_KEY = os.getenv("BINANCE_API_KEY")
    API_SECRET = os.getenv("BINANCE_SECRET_KEY")
    if not API_KEY or not API_SECRET:
        print("Error: API keys not set in environment or .env file.")
        exit(1)
    bot = ScalpingBot(API_KEY, API_SECRET)
    try:
        print("Running ScalpingBot v1 ...")
        asyncio.run(bot.main_loop())
    except KeyboardInterrupt:
        asyncio.run(bot.close())
        print("Bot closed.")