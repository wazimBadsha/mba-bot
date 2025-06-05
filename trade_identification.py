import asyncio
import ccxt.async_support as ccxt
import numpy as np
from collections import deque
from datetime import datetime, timedelta

class TradeIdentificationEngine:
    def __init__(self, binance_futures, symbol="ETH/USDT", timeframe_15m="15m"):
        self.exchange = binance_futures
        self.symbol = symbol
        self.timeframe_15m = timeframe_15m
        self.ohlcv_15m = deque(maxlen=50)
        self.ohlcv_1m = deque(maxlen=200)
        self.orderbook = {"bids": [], "asks": []}
        self.vwap_window_seconds = 60
        self.tick_prices = deque()
        self.close_buffer_1m = deque(maxlen=100)
        self.current_trade = None  # Track the current open trade

    async def fetch_historical(self):
        bars_15m = await self.exchange.fetch_ohlcv(self.symbol, timeframe=self.timeframe_15m, limit=50)
        for bar in bars_15m:
            self.ohlcv_15m.append(bar)
        bars_1m = await self.exchange.fetch_ohlcv(self.symbol, timeframe="1m", limit=200)
        for bar in bars_1m:
            self.ohlcv_1m.append(bar)
            self.close_buffer_1m.append(bar[4])

    def compute_vwap(self):
        now = datetime.utcnow()
        cutoff = now - timedelta(seconds=self.vwap_window_seconds)
        while self.tick_prices and self.tick_prices[0][0] < cutoff:
            self.tick_prices.popleft()
        pv_sum = sum(price * size for (ts, price, size, side) in self.tick_prices)
        volume_sum = sum(size for (ts, price, size, side) in self.tick_prices)
        return pv_sum / volume_sum if volume_sum > 0 else None

    def compute_rsi(self, closes, period=14):
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        if avg_loss == 0:
            return 100
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    async def on_new_15m_bar(self, bar):
        self.ohlcv_15m.append(bar)

    async def on_new_1m_bar(self, bar):
        self.ohlcv_1m.append(bar)
        self.close_buffer_1m.append(bar[4])

    async def on_orderbook_update(self, book):
        self.orderbook = book

    async def on_tick(self, price, size, side):
        self.tick_prices.append((datetime.utcnow(), price, size, side))

    def check_short_setup(self):
        latest_15m = self.ohlcv_15m[-1]
        close_15m = latest_15m[4]
        if not (2645 <= close_15m <= 2660):
            return False
        if len(self.close_buffer_1m) < 15:
            return False
        rsi_1m = self.compute_rsi(np.array(self.close_buffer_1m))
        if rsi_1m >= 50:
            return False
        vwap = self.compute_vwap()
        if vwap is None or vwap < close_15m:
            return False
        top_bids = sum(size for price, size in self.orderbook["bids"][:5])
        top_asks = sum(size for price, size in self.orderbook["asks"][:5])
        if top_asks < 1.5 * top_bids:
            return False
        return True

    async def run(self):
        """
        Main loop: fetch live events, update buffers, and check short setup.
        Uses polling since WebSocket is not supported.
        """
        while True:
            try:
                # Poll 15m and 1m bars
                bars_15m = await self.exchange.fetch_ohlcv(self.symbol, self.timeframe_15m, limit=1)
                await self.on_new_15m_bar(bars_15m[-1])
                
                bars_1m = await self.exchange.fetch_ohlcv(self.symbol, "1m", limit=1)
                await self.on_new_1m_bar(bars_1m[-1])
                
                # Poll orderbook
                orderbook = await self.exchange.fetch_order_book(self.symbol)
                await self.on_orderbook_update(orderbook)
                
                # After processing updates, check for a short setup
                if self.check_short_setup():
                    return {"signal": "short", "price": self.orderbook["asks"][0][0]}
                
                await asyncio.sleep(15)  # Poll every 15 seconds
                
            except Exception as e:
                print(f"Error in trade identification: {e}")
                await asyncio.sleep(5)  # Back off on error
                continue

    async def monitor_open_trade(self):
        """
        Poll ATR & current mark price every 5s to adjust stop/exit.
        """
        while self.current_trade and self.current_trade["status"] == "open":
            try:
                # Fetch latest 1m OHLC for ATR
                bars_1m = await self.exchange.fetch_ohlcv(self.symbol, "1m", limit=50)
                highs = [b[2] for b in bars_1m]
                lows = [b[3] for b in bars_1m]
                closes = [b[4] for b in bars_1m]
                atr = self.strategy_core.compute_atr(highs, lows, closes, window=14)
                mark_price = (await self.exchange.fetch_ticker(self.symbol))["last"]

                # Check if ATR-based exit triggers
                new_stop = self.strategy_core.adaptive_stop_loss(
                    self.current_trade["price"],
                    mark_price,
                    atr if atr else 0
                )
                
                if new_stop == "exit_now":
                    # Market exit
                    side = "BUY" if self.current_trade["signal"] == "short" else "SELL"
                    await self.exchange.create_market_order(
                        self.symbol,
                        side,
                        self.current_trade["quantity"]
                    )
                    
                    # Calculate P&L
                    pnl = (mark_price - self.current_trade["price"]) / self.current_trade["price"]
                    if self.current_trade["signal"] == "short":
                        pnl = -pnl
                    
                    # Log exit
                    msg = f"{datetime.now(UTC)} EXIT_AT_MARKET {side} price={mark_price} pnl={pnl:.4f}"
                    self.journal.write(msg + "\n")
                    
                    # Update stats
                    self.strategy_core.update_trade_return(pnl * 100)
                    self.risk_exec.update_trade_results(pnl * 100)
                    
                    self.current_trade = None
                    break

                await asyncio.sleep(5)
                
            except Exception as e:
                print(f"Error monitoring trade: {e}")
                await asyncio.sleep(5)
                continue