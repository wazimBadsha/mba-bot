import numpy as np
from collections import deque

class MathematicalStrategyCore:
    def __init__(self):
        self.closes_1m = deque(maxlen=50)
        self.returns_history = deque(maxlen=30)

    def update_1m_close(self, close_price):
        self.closes_1m.append(close_price)

    def compute_z_score(self, window=20):
        if len(self.closes_1m) < window:
            return None
        arr = np.array(self.closes_1m)
        mean = arr[-window:].mean()
        std = arr[-window:].std(ddof=0)
        if std == 0:
            return 0
        return (arr[-1] - mean) / std

    def compute_atr(self, highs, lows, closes, window=14):
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            trs.append(tr)
        if len(trs) < window:
            return None
        return np.mean(trs[-window:])

    def check_entry_zscore(self):
        z = self.compute_z_score(window=20)
        if z is None:
            return None
        if z > 2.0:
            return "short"
        elif z < -2.0:
            return "long"
        else:
            return None

    def adaptive_stop_loss(self, entry_price, current_price, atr):
        target_move = entry_price - 0.5 * atr
        if current_price <= target_move:
            return entry_price
        if current_price >= entry_price + 0.3 * atr:
            return "exit_now"
        return None

    def update_trade_return(self, pnl):
        self.returns_history.append(pnl)

    def rolling_sharpe(self):
        if len(self.returns_history) < 10:
            return None
        arr = np.array(self.returns_history)
        mean_r = arr.mean()
        std_r = arr.std(ddof=0)
        if std_r == 0:
            return float("inf")
        return mean_r / std_r

    def allow_new_trades(self, threshold=0.5):
        sr = self.rolling_sharpe()
        if sr is not None and sr < threshold:
            return False
        return True