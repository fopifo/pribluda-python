"""
Приблуда на python — монитор арбитражной связки (пары инструментов).

Два режима, задаются на каждую связку отдельно (arb_pairs.json):
  - "ratio_pct" — отношение цен (price_a / price_b), отклонение в %.
  - "absolute_rub" — разница цен (price_a - price_b) в рублях. Для пар
    вроде MTLR/MTLRP "прострел" (резкое расхождение спреда) — ХОРОШИЙ
    сигнал (возможность), а "схождение" обратно к норме — сигнал на
    выход из связки.

ГИСТЕРЕЗИС (защита от "флаппинга"): без него сигнал срабатывал заново
на каждой сделке, где отклонение хоть на миг проседало ниже порога, а
потом снова превышало его — в реальном логе это дало 6 срабатываний
"ПРОСТРЕЛ" за 12 секунд подряд по одному и тому же движению. Теперь:
  - "ПРОСТРЕЛ"/"РАСХОЖДЕНИЕ" — когда |отклонение| впервые превысило
    threshold (полный порог).
  - "СХОЖДЕНИЕ" — отдельный, противоположный сигнал: когда после
    прострела |отклонение| опустилось до RESET_FACTOR * threshold
    (по умолчанию половина порога) — это сигнал закрывать связку, а не
    просто "тишина".
  - Между RESET_FACTOR*threshold и threshold, пока уже сработал
    прострел — никаких новых сигналов, состояние просто "держится".

ПАМЯТЬ О ПОСЛЕДНЕМ СОБЫТИИ: GUI опрашивает snapshot() раз в несколько
секунд, а событие могло произойти и уже смениться между двумя опросами.
snapshot(now_ts) поэтому помнит последнее событие ещё DISPLAY_HOLD_SEC
секунд после его фактического момента — так GUI гарантированно успеет
его показать, даже если "живое" состояние к моменту опроса уже другое.

Обычный уровень (baseline) считается через EMA (half_life_sec — период
полураспада), обновляется на каждой сделке ЛЮБОЙ из двух ног, кроме
периода активного прострела (иначе EMA "подтянется" к аномалии и
сигнал пропадёт сам собой).
"""

from dataclasses import dataclass

DISPLAY_HOLD_SEC = 30.0


@dataclass
class ArbSignal:
    pair_name: str
    symbol_a: str
    symbol_b: str
    mode: str            # "ratio_pct" или "absolute_rub"
    kind: str             # "prostrel" | "divergence" | "convergence"
    price_a: float
    price_b: float
    value: float          # отношение или спред в руб
    baseline: float
    deviation: float      # проценты (ratio_pct) или рубли (absolute_rub)
    ts: float
    is_opportunity: bool  # True для absolute_rub — это "хорошо", не тревога

    def __str__(self) -> str:
        labels = {
            "prostrel": "ПРОСТРЕЛ",
            "divergence": "РАСХОЖДЕНИЕ",
            "convergence": "СХОЖДЕНИЕ",
        }
        label = labels[self.kind]
        if self.mode == "ratio_pct":
            return (
                f"[арбитраж:{self.pair_name}:{label}] {self.symbol_a}/{self.symbol_b} "
                f"{self.symbol_a}={self.price_a:.2f} {self.symbol_b}={self.price_b:.2f} "
                f"отношение={self.value:.4f} обычное={self.baseline:.4f} "
                f"отклонение={self.deviation:+.2f}%"
            )
        return (
            f"[арбитраж:{self.pair_name}:{label}] {self.symbol_a}-{self.symbol_b} "
            f"{self.symbol_a}={self.price_a:.2f} {self.symbol_b}={self.price_b:.2f} "
            f"спред={self.value:.2f}₽ обычный={self.baseline:.2f}₽ "
            f"отклонение={self.deviation:+.2f}₽"
        )


class PairMonitor:
    """Монитор одной арбитражной связки: symbol_a / symbol_b."""

    RESET_FACTOR = 0.5  # доля от threshold, при которой считаем "сошлось"

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
        self.threshold = threshold
        self.half_life_sec = half_life_sec

        self.last_price_a: float | None = None
        self.last_price_b: float | None = None

        self.baseline: float | None = None
        self._baseline_ts: float | None = None

        self.triggered = False

        self.last_event_kind: str | None = None
        self.last_event_ts: float | None = None
        self.last_event_deviation: float | None = None

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
        if self.mode == "ratio_pct":
            if not self.baseline:
                return 0.0
            return (value - self.baseline) / self.baseline * 100
        return value - self.baseline

    def _make_signal(self, kind: str, value: float, deviation: float, ts: float) -> ArbSignal:
        self.last_event_kind = kind
        self.last_event_ts = ts
        self.last_event_deviation = deviation
        return ArbSignal(
            pair_name=self.pair_name,
            symbol_a=self.symbol_a,
            symbol_b=self.symbol_b,
            mode=self.mode,
            kind=kind,
            price_a=self.last_price_a,
            price_b=self.last_price_b,
            value=value,
            baseline=self.baseline,
            deviation=deviation,
            ts=ts,
            is_opportunity=(self.mode == "absolute_rub"),
        )

    def on_trade(self, symbol: str, price: float, ts: float) -> "ArbSignal | None":
        """Кормим сюда каждую сделку по symbol_a ИЛИ symbol_b. Возвращает
        ArbSignal при переходе через порог (прострел/расхождение) или при
        возврате к норме (схождение) — с гистерезисом, см. докстринг."""
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
        reset_level = self.threshold * self.RESET_FACTOR

        if not self.triggered and abs(deviation) >= self.threshold:
            self.triggered = True
            kind = "prostrel" if self.mode == "absolute_rub" else "divergence"
            return self._make_signal(kind, value, deviation, ts)

        if self.triggered and abs(deviation) <= reset_level:
            self.triggered = False
            return self._make_signal("convergence", value, deviation, ts)

        return None

    def snapshot(self, now_ts: float | None = None) -> dict:
        """Текущее состояние связки для отображения в GUI — только
        чтение, не влияет на логику. Если now_ts передан и последнее
        событие было не более DISPLAY_HOLD_SEC секунд назад — включает
        его в снимок отдельно (last_event_*), чтобы GUI не пропустил
        короткоживущее событие между двумя опросами."""
        value = self._current_value()
        deviation = self._deviation(value) if value is not None and self.baseline is not None else None

        recent_event_kind = None
        if now_ts is not None and self.last_event_ts is not None:
            if now_ts - self.last_event_ts <= DISPLAY_HOLD_SEC:
                recent_event_kind = self.last_event_kind

        return {
            "pair_name": self.pair_name,
            "symbol_a": self.symbol_a,
            "symbol_b": self.symbol_b,
            "mode": self.mode,
            "price_a": self.last_price_a,
            "price_b": self.last_price_b,
            "current_value": value,
            "baseline": self.baseline,
            "deviation": deviation,
            "threshold": self.threshold,
            "triggered": self.triggered,
            "is_opportunity": (self.mode == "absolute_rub"),
            "recent_event_kind": recent_event_kind,
        }