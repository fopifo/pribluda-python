"""
Приблуда на python — живой трекер спреда арбитражной связки.
Спред + EMA-база с заданным периодом полураспада (half_life_sec).
Архитектура: modules/arbitrage/. Не торгует, не выставляет ордера.
"""
from collections import deque


class PairTracker:
    def __init__(self, half_life_sec=600.0, history_len=1080):
        self.half_life_sec = float(half_life_sec) if half_life_sec else 600.0
        self.ema = None
        self.spread = None
        self.last_ts = None
        self.history = deque(maxlen=int(history_len))

    def update(self, spread, ts):
        if self.ema is None:
            self.ema = spread
        elif self.last_ts is not None and ts > self.last_ts:
            dt = ts - self.last_ts
            alpha = 1.0 - 0.5 ** (dt / self.half_life_sec)
            self.ema += alpha * (spread - self.ema)
        self.spread = spread
        self.last_ts = ts
        self.history.append((ts, spread, self.ema))

    def deviation(self):
        if self.spread is None or self.ema is None:
            return None
        return self.spread - self.ema


def compute_spread(mode, price_a, price_b):
    """absolute_rub: b - a; ratio_pct: (b/a - 1) * 100."""
    if price_a is None or price_b is None:
        return None
    if mode == "ratio_pct":
        if price_a <= 0:
            return None
        return (price_b / price_a - 1.0) * 100.0
    return price_b - price_a