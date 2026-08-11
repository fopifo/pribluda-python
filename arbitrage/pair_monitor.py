"""
Приблуда на python — монитор арбитражной связки (пары инструментов).

Отслеживает отношение цен двух инструментов (например, MTLRP/MTLR) в
реальном времени, на каждой новой сделке любой из двух ног. Обычный
уровень отношения считается через экспоненциальное скользящее среднее
(EMA) — обновляется на каждой сделке за O(1), без хранения истории цен.
Сигнал — когда текущее отношение отклонилось от EMA больше порога.

EMA настраивается через half-life (период полураспада) в секундах.
half_life_sec=600 (10 минут) означает, что вклад сделки 10-минутной
давности в текущее среднее уменьшился вдвое.

Анти-спам: пока расхождение держится выше порога, повторный сигнал не
выдаётся (self.triggered) — до возврата отношения внутрь порога.
Пока идёт активное расхождение, baseline НЕ обновляется — иначе EMA
сама "подтянется" к аномальной цене и расхождение исчезнет само собой.
"""

from dataclasses import dataclass


@dataclass
class ArbSignal:
    pair_name: str
    symbol_a: str
    symbol_b: str
    ratio: float
    baseline: float
    deviation_pct: float
    ts: float

    def __str__(self) -> str:
        return (
            f"[арбитраж:{self.pair_name}] {self.symbol_a}/{self.symbol_b} "
            f"текущее={self.ratio:.4f} обычное={self.baseline:.4f} "
            f"отклонение={self.deviation_pct:+.2f}%"
        )


class PairMonitor:
    """Монитор одной арбитражной связки: symbol_a / symbol_b."""

    def __init__(
        self,
        pair_name: str,
        symbol_a: str,
        symbol_b: str,
        threshold_pct: float = 1.5,
        half_life_sec: float = 600.0,
    ):
        self.pair_name = pair_name
        self.symbol_a = symbol_a
        self.symbol_b = symbol_b
        self.threshold_pct = threshold_pct
        self.half_life_sec = half_life_sec

        self.last_price_a: float | None = None
        self.last_price_b: float | None = None

        self.baseline: float | None = None  # EMA текущего отношения
        self._baseline_ts: float | None = None

        self.triggered = False  # анти-спам: сигнал уже выдан, ждём возврата в норму

    def _update_baseline(self, ratio: float, ts: float) -> None:
        if self.baseline is None:
            self.baseline = ratio
            self._baseline_ts = ts
            return

        dt = max(ts - self._baseline_ts, 0.0)
        alpha = 1 - 0.5 ** (dt / self.half_life_sec) if self.half_life_sec > 0 else 1.0
        self.baseline = self.baseline + alpha * (ratio - self.baseline)
        self._baseline_ts = ts

    def on_trade(self, symbol: str, price: float, ts: float) -> "ArbSignal | None":
        """Кормим сюда каждую сделку по symbol_a ИЛИ symbol_b (какая
        пришла). Возвращает ArbSignal, если расхождение только что
        превысило порог (не повторяется, пока не вернётся в норму)."""
        if symbol == self.symbol_a:
            self.last_price_a = price
        elif symbol == self.symbol_b:
            self.last_price_b = price
        else:
            return None

        if self.last_price_a is None or self.last_price_b is None:
            return None

        ratio = self.last_price_a / self.last_price_b

        if not self.triggered:
            self._update_baseline(ratio, ts)

        if self.baseline is None or self.baseline == 0:
            return None

        deviation_pct = (ratio - self.baseline) / self.baseline * 100

        if abs(deviation_pct) >= self.threshold_pct:
            if self.triggered:
                return None
            self.triggered = True
            return ArbSignal(
                pair_name=self.pair_name,
                symbol_a=self.symbol_a,
                symbol_b=self.symbol_b,
                ratio=ratio,
                baseline=self.baseline,
                deviation_pct=deviation_pct,
                ts=ts,
            )
        else:
            self.triggered = False
            return None

    def snapshot(self) -> dict:
        """Текущее состояние связки для отображения в GUI (вкладка
        "Арбитраж") — только чтение, ничего не меняет и не влияет на
        логику детекции в on_trade. Если ещё не видели цену по обеим
        ногам, current_ratio будет None."""
        current_ratio = None
        if self.last_price_a is not None and self.last_price_b is not None:
            current_ratio = self.last_price_a / self.last_price_b

        deviation_pct = None
        if current_ratio is not None and self.baseline:
            deviation_pct = (current_ratio - self.baseline) / self.baseline * 100

        return {
            "pair_name": self.pair_name,
            "symbol_a": self.symbol_a,
            "symbol_b": self.symbol_b,
            "current_ratio": current_ratio,
            "baseline": self.baseline,
            "deviation_pct": deviation_pct,
            "threshold_pct": self.threshold_pct,
            "triggered": self.triggered,
        }