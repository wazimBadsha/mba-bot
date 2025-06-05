import asyncio
import time
import numpy as np
from collections import deque

class RiskExecutionLayer:
    def __init__(self, exchange, symbol="ETH/USDT", max_risk_usd=100):
        self.exchange = exchange
        self.symbol = symbol
        self.max_risk = max_risk_usd

        # Running stats for dynamic sizing
        self.trade_results = deque(maxlen=200)  # store floats: positive or negative %
        self.win_count = 0
        self.loss_count = 0

    def estimate_kelly_fraction(self):
        """
        Rough Kelly: f = (p*(R+1) - 1)/R  where p = win_prob, R = avg_reward/risk
        """
        if len(self.trade_results) < 20:
            return 0.001  # minimal size until stats build
        arr = np.array(self.trade_results)
        wins = arr[arr > 0]
        losses = arr[arr <= 0]
        p = len(wins) / len(arr)
        if len(losses) == 0 or np.mean(losses) == 0:
            return 0.002  # very small if no losing data
        avg_win = wins.mean() if len(wins) > 0 else 0.0
        avg_loss = -losses.mean()  # positive number
        R = avg_win / avg_loss if avg_loss > 0 else 1
        f = (p * (R + 1) - 1) / R
        return max(min(f, 0.01), 0.0005)  # clamp between 0.05% – 1.0% of account

    async def micro_stop_filter(self, order_id, entry_price, quantity, side, max_adverse=0.003, max_ms=500):
        """
        Watch order’s fill. If price moves against us by > max_adverse% within max_ms, cancel trade.
        side = "short" or "long"
        """
        start = time.time() * 1000  # ms
        while True:
            # fetch latest mark price
            ticker = await self.exchange.fetch_ticker(self.symbol)
            mark_price = float(ticker["last"])
            elapsed = time.time() * 1000 - start
            adverse_move = (mark_price - entry_price) / entry_price if side == "long" else (entry_price - mark_price) / entry_price
            if adverse_move >= max_adverse:
                # Cancel open position or order
                await self.exchange.cancel_order(order_id, self.symbol)
                return {"exit": "micro_stop", "price": mark_price}
            if elapsed > max_ms:
                break
            await asyncio.sleep(0.01)
        return {"exit": None}

    async def place_scaled_order(self, side, price, quantity):
        """
        Place a post-only limit order at the specified price. If not filled in 300 ms, cancel.
        """
        params = {"timeInForce": "GTC", "type": "LIMIT", "side": "SELL" if side == "short" else "BUY",
                  "quantity": quantity, "price": price, "newOrderRespType": "RESULT", "postOnly": True}
        order = await self.exchange.create_order(self.symbol, **params)
        # Wait a short time for possible fill
        await asyncio.sleep(0.3)
        # Check status
        status = await self.exchange.fetch_order(order["id"], self.symbol)
        if status["status"] != "FILLED":
            await self.exchange.cancel_order(order["id"], self.symbol)
            return None  # order not executed
        return order

    def update_trade_results(self, pnl_pct):
        self.trade_results.append(pnl_pct)

    async def execute_trade(self, signal, entry_price):
        """
        signal = {"signal": "short"/"long", "price": entry_price}
        Use Kelly fraction to size. Then call micro_stop_filter. Then track P/L.
        """
        # 1. Estimate position size in USDT (notional) via Kelly
        fraction = self.estimate_kelly_fraction()
        # Suppose account size is $10 000 (hardcoded for example)
        account_size = 10000
        max_notional = min(account_size * fraction, self.max_risk)  # Never risk more than max_risk_usd
        # Convert to ETH quantity (approx)
        quantity = max_notional / entry_price

        # Never open a position if quantity is too large or too small (avoid liquidation risk)
        if quantity <= 0 or max_notional > self.max_risk:
            return None  # Block trade, too risky

        # 2. Place initial order
        order = await self.place_scaled_order(signal["signal"], entry_price, quantity)
        if not order:
            return None  # failed to get filled

        # 3. Start micro stop filter
        micro_exit = await self.micro_stop_filter(order["id"], entry_price, quantity, signal["signal"])
        if micro_exit["exit"] == "micro_stop":
            # record a small loss
            pnl = (micro_exit["price"] - entry_price) / entry_price if signal["signal"] == "long" else (entry_price - micro_exit["price"]) / entry_price
            self.update_trade_results(pnl * 100)  # convert to percent
            return {"status": "stopped", "price": micro_exit["price"], "pnl": pnl}

        # 4. If no micro‐stop triggered, then we rely on the strategy core for ATR‐based exit (to be polled externally)
        return {"status": "open", "price": entry_price, "quantity": quantity, "signal": signal["signal"]}