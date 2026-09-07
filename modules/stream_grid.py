"""
Приблуда на python — StreamGrid v2.1: стриминг-детектор сеток роботов.
Исправлено по ревью 2026-09-05: ключ буфера — (symbol, side), НЕ (…, qty).
Объёмы группируются в кластеры (<=2 вариантов, ratio<=1.10) ВНУТРИ стороны,
поэтому чередующий робот (45/46) остаётся ОДНОЙ серией с верным периодом p,
а не двумя сериями с периодом 2p. Сигнал — на min_repeats-м ударе кластера.
v2.1: допуск tol — параметр конструктора (тюнинг порогов по ревью п.6).
interval_robot.py НЕ трогает (чистое A/B по ревью п.10).
"""
from collections import deque, defaultdict

P_MIN, P_MAX = 2.0, 120.0
GAP_LO, GAP_HI = 1.0, 120.0
MAX_CLUSTERS = 40
MAX_QTY_VARIANTS = 2
QTY_RATIO = 1.10
CLUSTER_BUF = 40


class _Cluster:
    __slots__ = ("qs", "ts")

    def __init__(self, qty, ts):
        self.qs = {qty}
        self.ts = deque(maxlen=CLUSTER_BUF)
        self.ts.append(ts)


class StreamGrid:
    def __init__(self, min_repeats=4, check_every=4, tol=0.12):
        self.min_repeats = min_repeats
        self.check_every = check_every
        self.tol = tol
        self.sides = defaultdict(list)
        self.emitted = {}
        self._n = defaultdict(int)

    def _fit(self, clusters, qty):
        for c in clusters:
            if qty in c.qs:
                return c
        for c in clusters:
            if len(c.qs) < MAX_QTY_VARIANTS:
                lo = min(min(c.qs), qty)
                hi = max(max(c.qs), qty)
                if hi / lo <= QTY_RATIO:
                    c.qs.add(qty)
                    return c
        return None

    def on_trade(self, sym, side, qty, ts):
        key = (sym, side)
        clusters = self.sides[key]
        c = self._fit(clusters, qty)
        if c is None:
            clusters.append(_Cluster(qty, ts))
            if len(clusters) > MAX_CLUSTERS:
                clusters.sort(key=lambda x: x.ts[-1] if x.ts else 0)
                clusters.pop(0)
            return None
        c.ts.append(ts)
        self._n[key] += 1
        if len(c.ts) < self.min_repeats:
            return None
        if self._n[key] % self.check_every:
            return None
        return self._test(sym, side, c, ts)

    def _test(self, sym, side, c, ts):
        t = list(c.ts)
        gaps = [(t[i + 1] - t[i]) / 1000 for i in range(len(t) - 1)]
        small = sorted(g for g in gaps if GAP_LO <= g <= GAP_HI)
        if len(small) < 3:
            return None
        p = small[len(small) // 2]
        if not (P_MIN <= p <= P_MAX):
            return None
        tail = gaps[-(self.min_repeats - 1):]
        single = 0
        for g in tail:
            k = round(g / p)
            if k < 1 or abs(g - k * p) > max(k * p * self.tol, 0.7):
                return None
            if k == 1:
                single += 1
        if single < 2:
            return None
        ekey = (sym, side, tuple(sorted(c.qs)))
        last = self.emitted.get(ekey)
        if last is not None and ts - last < 4 * p * 1000:
            return None
        self.emitted[ekey] = ts
        return {"ticker": sym, "side": side,
                "qty_min": min(c.qs), "qty_max": max(c.qs),
                "period": p, "start_ms": t[-self.min_repeats],
                "end_ms": ts, "repeats": self.min_repeats}