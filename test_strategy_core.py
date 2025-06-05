import pytest
from strategy_core import MathematicalStrategyCore

def test_z_score():
    core = MathematicalStrategyCore()
    for i in range(50):
        core.update_1m_close(100 + i)
    z = core.compute_z_score(window=20)
    assert z is not None

def test_atr():
    core = MathematicalStrategyCore()
    highs = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24]
    lows = [9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]
    closes = [9.5, 10.5, 11.5, 12.5, 13.5, 14.5, 15.5, 16.5, 17.5, 18.5, 19.5, 20.5, 21.5, 22.5, 23.5]
    atr = core.compute_atr(highs, lows, closes, window=14)
    assert atr is not None