"""
Приблуда на python — StreamGrid v2.2: стриминг-детектор сеток роботов.
Управлено по ревью 2026-09-05: ключ буфера — (symbol, side), НЕ (…, qty).
Объёмы группируются в кластеры (<=2 вариантов, ratio<=1.10) ВНУТРИ стороны,
поэтому чередующий робот (45/46) остаётся ОДНОЙ серией с верным периодом p,
а не двумя сериями с периодом 2p. Сигнал — на min_repeats-м ударе кластера.
v2.1: допуск tol — параметр конструктора (тюнинг порогов по ревью п.6).
v2.2 (2026-09-07): миллисекундный гейт устойчивости (см. _ms_jitter_ok).
    Секундный допуск (tol) отсекает грубый шум быстро; миллисекундный
    гейт — тонкое сито против burst/случайных совпадений: настоящий
    программный робот на таймере держит интервал с точностью до мс,
    случайные серии — разброс мс-остатков велик. Оба гейта вместе.
    Побочный эффект: ms_hits в сигнале — для GUI .MS и обнаружения
    "пачек" (несколько тикеров бьют в одну миллисекунду одновременно).
interval_robot.py НЕ трогает (чистое A/B по ревью п.10).
"""
from collections import deque, defaultdict

P_MIN, P_MAX = 2.0, 120.0
GAP_LO, GAP_HI = 1.0, 120.0
MAX_CLUSTERS = 40
MAX_QTY_VARIANTS = 2
QTY_RATIO = 1.10
CLUSTER_BUF = 40

# Миллисекундный порог стабильности. Меньше = строже (меньше FP, но
# может резать реальных роботов с грубым таймером). Тюнить вместе с
# min_repeats/tol, не вместо.
MS_JITTER_MAX = 150.0


def _ms_jitter_ok(gaps_sec, period_sec):
    """Считает миллисекундный остаток каждого гэпа относительно периода
    (отклонение гэпа от ближайшего кратного периода, свёрнутое в
    [0, period/2] — "насколько не попал", а не абсолютный остаток по
    модулю). Возвращает (ok: bool, ms_hits: list[float])."""
    if period_sec <= 0:
        return False, []
    period_ms = period_sec * 1000.0
    ms_hits = []
    for g in gaps_sec:
        g_ms = g * 1000.0
        k = round(g_ms / period_ms)
        if k < 1:
            continue
        dev = abs(g_ms - k * period_ms)
        ms_hits.append(dev)
    if len(ms_hits) < 2:
        return False, ms_hits
    # Требуем стабильность для БОЛЬШИНСТВА последних гэпов (60%),
    # не для всех разом — одна шумная сделка не должна рушить серию.
    good = sum(1 for d in ms_hits if d <= MS_JITTER_MAX)
    ratio_ok = good / len(ms_hits) >= 0.6
    return ratio_ok, ms_hits


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

        # НОВЫЙ ГЕЙТ: миллисекундная устойчивость. Работает ПОСЛЕ
        # секундного допуска — дополнительное, более строгое сито.
        ms_ok, ms_hits = _ms_jitter_ok(tail, p)
        if not ms_ok:
            return None

        ekey = (sym, side, tuple(sorted(c.qs)))
        last = self.emitted.get(ekey)
        if last is not None and ts - last < 4 * p * 1000:
            return None
        self.emitted[ekey] = ts
        return {
            "ticker": sym,
            "side": side,
            "qty_min": min(c.qs),
            "qty_max": max(c.qs),
            "period": p,
            "start_ms": t[-self.min_repeats],
            "end_ms": ts,
            "repeats": self.min_repeats,
            # Миллисекундные остатки (последние 3) — для GUI .MS и
            # обнаружения "пачек" (общая миллисекунда у разных тикеров).
            "ms_hits": [round(d) for d in ms_hits[-3:]],
        }