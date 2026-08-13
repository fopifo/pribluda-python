"""
Приблуда на python — монитор арбитражной связки (пары инструментов).

Два режима, задаются на каждую связку отдельно (arb_pairs.json):

  - "ratio_pct" (по умолчанию) — отслеживает ОТНОШЕНИЕ цен
    (price_a / price_b) и его отклонение в ПРОЦЕНТАХ от обычного
    уровня. Подходит, когда важно относительное соотношение, а не
    абсолютная разница в деньгах.

  - "absolute_rub" — отслеживает АБСОЛЮТНУЮ разницу цен
    (price_a - price_b) в РУБЛЯХ от обычного уровня. Для пар вроде
    MTLR/MTLRP, где привилегированная акция двигается заметно
    медленнее обычной — "прострел" (резкое расхождение спреда) в этом
    режиме считается ХОРОШИМ сигналом (торговой возможностью), а не
    тревогой, поэтому и сообщение формулируется позитивно ("ПРОСТРЕЛ"),
    а не как предупреждение ("РАСХОЖДЕНИЕ").

В обоих режимах — та же механика: обычный уровень считается через EMA
(экспоненциальное скользящее среднее, half_life_sec — период
полураспада), обновляется на каждой сделке любой из двух ног, кроме
периода активного отклонения (иначе EMA "подтянется" к аномалии и
сигнал пропадёт сам собой). Анти-спам: пока отклонение держится выше
порога, повторный сигнал не выдаётся, пока не вернётся в норму.
"""

from dataclasses import dataclass


@dataclass
class ArbSignal:
    pair_name: str
    symbol_a: str
    symbol_b: str
    mode: str          # "ratio_pct" или "absolute_rub"
    value: float        # текущее значение (отношение или спред в руб)
    baseline: float      # обычный уровень (EMA)
    deviation: float     # отклонение: проценты (ratio_pct) или рубли (absolute_rub)
    ts: float
    is_opportunity: bool  # True для absolute_rub — это "хорошо", не тревога

    def __str__(self) -> str:
        label = "ПРОСТРЕЛ" if self.is_opportunity else "РАСХОЖДЕНИЕ"
        if self.mode == "ratio_pct":
            return (
                f"[арбитраж:{self.pair_name}:{label}] {self.symbol_a}/{self.symbol_b} "
                f"текущее={self.value:.4f} обычное={self.baseline:.4f} "
                f"отклонение={self.deviation:+.2f}%"
            )
        return (
            f"[арбитраж:{self.pair_name}:{label}] {self.symbol_a}-{self.symbol_b} "
            f"спред={self.value:.2f}₽ обычный={self.baseline:.2f}₽ "
            f"отклонение={self.deviation:+.2f}₽"
        )


class PairMonitor:
    """Монитор одной арбитражной связки: symbol_a / symbol_b."""

    def __init__(
        self,
        pair_name: str,
        symbol_a: str,
        symbol_b: str,
        mode: str = "ratio_pct",
        threshold: float = 1.5,
        half_life_sec: float = 600.0,
    ):
        self.pair_name = pair_name
        self.symbol_a = symbol_a
        self.symbol_b = symbol_b
        self.mode = mode
        self.threshold = threshold  # проценты (ratio_pct) или рубли (absolute_rub)
        self.half_life_sec = half_life_sec

        self.last_price_a: float | None = None
        self.last_price_b: float | None = None

        self.baseline: float | None = None
        self._baseline_ts: float | None = None

        self.triggered = False

    def _current_value(self) -> float | None:
        if self.last_price_a is None or self.last_price_b is None:
            return None
        if self.mode == "absolute_rub":
            return self.last_price_a - self.last_price_b
        return self.last_price_a / self.last_price_b

    def _update_baseline(self, value: float, ts: float) -> None:
        if self.baseline is None:
            self.baseline = value
            self._baseline_ts = ts
            return

        dt = max(ts - self._baseline_ts, 0.0)
        alpha = 1 - 0.5 ** (dt / self.half_life_sec) if self.half_life_sec > 0 else 1.0
        self.baseline = self.baseline + alpha * (value - self.baseline)
        self._baseline_ts = ts

    def _deviation(self, value: float) -> float:
        """Возвращает отклонение в единицах, в которых задан порог:
        проценты для ratio_pct, рубли для absolute_rub."""
        if self.mode == "ratio_pct":
            if not self.baseline:
                return 0.0
            return (value - self.baseline) / self.baseline * 100
        return value - self.baseline

    def on_trade(self, symbol: str, price: float, ts: float) -> "ArbSignal | None":
        """Кормим сюда каждую сделку по symbol_a ИЛИ symbol_b (какая
        пришла). Возвращает ArbSignal, если отклонение только что
        превысило порог (не повторяется, пока не вернётся в норму)."""
        if symbol == self.symbol_a:
            self.last_price_a = price
        elif symbol == self.symbol_b:
            self.last_price_b = price
        else:
            return None

        value = self._current_value()
        if value is None:
            return None

        if not self.triggered:
            self._update_baseline(value, ts)

        if self.baseline is None:
            return None

        deviation = self._deviation(value)

        if abs(deviation) >= self.threshold:
            if self.triggered:
                return None
            self.triggered = True
            return ArbSignal(
                pair_name=self.pair_name,
                symbol_a=self.symbol_a,
                symbol_b=self.symbol_b,
                mode=self.mode,
                value=value,
                baseline=self.baseline,
                deviation=deviation,
                ts=ts,
                is_opportunity=(self.mode == "absolute_rub"),
            )
        else:
            self.triggered = False
            return None

    def snapshot(self) -> dict:
        """Текущее состояние связки для отображения в GUI — только
        чтение, не влияет на логику."""
        value = self._current_value()
        deviation = self._deviation(value) if value is not None and self.baseline is not None else None

        return {
            "pair_name": self.pair_name,
            "symbol_a": self.symbol_a,
            "symbol_b": self.symbol_b,
            "mode": self.mode,
            "current_value": value,
            "baseline": self.baseline,
            "deviation": deviation,
            "threshold": self.threshold,
            "triggered": self.triggered,
            "is_opportunity": (self.mode == "absolute_rub"),
        }