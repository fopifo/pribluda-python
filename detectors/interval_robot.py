"""
Приблуда на python — универсальный детектор периодичности ("роботов").

Серия сделок с одинаковой стороной (side), повторяющаяся с определённым
интервалом. Три режима матчинга объёма/интервала, задаются пресетом:

  - "loose" (широкий): любая сделка, попавшая в абсолютный диапазон
    [min_interval, max_interval] от предыдущей, продлевает серию.

  - "strict" (чёткий, через interval_tolerance): первая пара сделок
    задаёт "эталонный" интервал для этой конкретной серии, а каждая
    следующая сделка должна укладываться в него ± interval_tolerance
    от ПРЕДЫДУЩЕГО фактического интервала в этой же серии. Объём при
    этом должен совпадать (max_qty_variants=1).

  - "TWAP" (ignore_qty=True): как strict по интервалу (обычно с более
    жёстким interval_tolerance, например 0.03 вместо 0.1), но объём
    ВООБЩЕ не участвует в матчинге — любая сделка нужной стороны с
    подходящим интервалом продлевает серию, независимо от размера.
    Это ловит паттерн "крупная заявка, нарезанная по времени на равные
    интервалы, с разным объёмом каждый раз" (найдено на реальных логах
    по PLZL и BELU — десятки "параллельных" сигналов с одинаковым
    таймингом и разным объёмом, которые strict-режим дробил на
    отдельные несвязанные серии из-за требования точного объёма).

    Поскольку объём здесь не фильтрует случайные совпадения, вся
    ответственность за отсев случайности ложится на ТОЧНОСТЬ ИНТЕРВАЛА
    (interval_tolerance) и число повторов (min_repeats) — оба должны
    быть строже, чем в обычном strict, иначе легко словить шум. Идея:
    ни человек, ни случайное совпадение разных участников не могут
    держать интервал в пределах нескольких процентов МНОГО раз подряд
    — а чистый программный цикл может.

Фильтр входа (min_qty) — объём в лотах, низкий пол (не потолок).

ДЖИТТЕР: каждый Candidate копит полный список интервалов между
сделками серии — при закрытии серии считаем по нему стандартное
отклонение в миллисекундах (Signal.jitter_ms). Только измерение
постфактум, не влияет на матчинг/продление серии.

Для живого режима: on_trade() реагирует на приход сделки. check_overdue()
вызывается по таймеру (watchdog), не привязанному к потоку сделок — но
использует РОВНО ТЕ ЖЕ границы допуска, что и сам матчинг в
_interval_fits, чтобы не "опережать" реальную логику детектора.

Производительность: для strict/loose кандидаты индексируются по объёму
(qty) для быстрого точного совпадения. Для TWAP (ignore_qty=True) объём
не индексируется — поиск линейный по активным сериям этой стороны, но
это не проблема: MAX_ACTIVE_PER_SIDE всё равно ограничивает худший
случай, а число одновременно живых серий на практике невелико.
"""

import statistics
from datetime import datetime, timezone

from .base import Detector, Signal


class Candidate:
    """Одна потенциальная серия сделок одного робота, ещё не закрытая."""

    __slots__ = (
        "qty_variants", "count", "start_ts", "last_ts",
        "last_interval", "intervals", "warned",
    )

    def __init__(self, qty: int, ts: float):
        self.qty_variants: set[int] = {qty}
        self.count = 1
        self.start_ts = ts
        self.last_ts = ts
        self.last_interval: float | None = None  # появится после 2-й сделки
        self.intervals: list[float] = []  # полная история интервалов серии, для джиттера
        self.warned = False  # предупреждение о просрочке уже выдавалось?


class IntervalRobotDetector(Detector):
    name = "робот-интервал"

    MAX_SERIES_LENGTH = 20      # после ~20 повторов считаем серию завершённой
    MAX_ACTIVE_PER_SIDE = 3000  # предохранитель от неограниченного роста
    CLOSE_AFTER_MISSES = 2      # сколько пропущенных max_interval ждать до удаления

    def __init__(self, symbol: str, settings: dict):
        super().__init__(symbol, settings)
        self.min_qty = settings["min_qty"]
        self.min_repeats = settings["min_repeats"]
        self.min_interval = settings["min_interval"]
        self.max_interval = settings["max_interval"]
        self.max_qty_variants = settings.get("max_qty_variants", 2)
        self.max_qty_ratio = settings.get("max_qty_ratio")
        self.interval_tolerance = settings.get("interval_tolerance")
        self.ignore_qty = settings.get("ignore_qty", False)

        preset_name = settings.get("preset_name")
        self.preset_name = preset_name or ""
        if preset_name:
            self.name = f"робот-интервал[{preset_name}]"

        self.active: dict[str, list[Candidate]] = {}
        self.index: dict[str, dict[int, list[Candidate]]] = {}

    def _finalize(self, side: str, candidate: Candidate) -> Signal:
        avg_interval = (candidate.last_ts - candidate.start_ts) / max(candidate.count - 1, 1)

        jitter_ms = None
        if len(candidate.intervals) >= 2:
            jitter_ms = statistics.pstdev(candidate.intervals) * 1000

        return Signal(
            detector_name=self.name,
            symbol=self.symbol,
            side=side,
            qty_variants=sorted(candidate.qty_variants),
            repeats=candidate.count,
            interval_avg=avg_interval,
            start_ts=candidate.start_ts,
            end_ts=candidate.last_ts,
            jitter_ms=jitter_ms,
        )

    def _register(self, side: str, candidate: Candidate) -> None:
        self.active.setdefault(side, []).append(candidate)
        if self.ignore_qty:
            return  # не индексируем по объёму — он не участвует в матчинге
        side_index = self.index.setdefault(side, {})
        for qty in candidate.qty_variants:
            side_index.setdefault(qty, []).append(candidate)

    def _unregister(self, side: str, candidate: Candidate) -> None:
        active_list = self.active.get(side)
        if active_list and candidate in active_list:
            active_list.remove(candidate)
        if self.ignore_qty:
            return
        side_index = self.index.get(side, {})
        for qty in candidate.qty_variants:
            bucket = side_index.get(qty)
            if bucket and candidate in bucket:
                bucket.remove(candidate)
                if not bucket:
                    del side_index[qty]

    def _index_new_variant(self, side: str, candidate: Candidate, qty: int) -> None:
        if self.ignore_qty:
            return
        self.index.setdefault(side, {}).setdefault(qty, []).append(candidate)

    def _prune_dead(self, side: str, now_ts: float) -> list[Signal]:
        """Закрывает серии, по которым пропущено CLOSE_AFTER_MISSES
        интервалов max_interval (ожидаем второй пропуск, а не первый)."""
        signals: list[Signal] = []
        threshold = self.max_interval * self.CLOSE_AFTER_MISSES
        for candidate in list(self.active.get(side, [])):
            if now_ts - candidate.last_ts > threshold:
                if candidate.count >= self.min_repeats:
                    signals.append(self._finalize(side, candidate))
                self._unregister(side, candidate)
        return signals

    def _enforce_cap(self, side: str) -> list[Signal]:
        active_list = self.active.get(side, [])
        overflow = len(active_list) - self.MAX_ACTIVE_PER_SIDE
        if overflow <= 0:
            return []

        to_evict = sorted(active_list, key=lambda c: (c.count, c.start_ts))[:overflow]

        signals: list[Signal] = []
        for candidate in to_evict:
            if candidate.count >= self.min_repeats:
                signals.append(self._finalize(side, candidate))
            self._unregister(side, candidate)
        return signals

    def _qty_fits_loose(self, candidate: Candidate, qty: int) -> bool:
        if self.max_qty_ratio is None:
            return True
        low = min(*candidate.qty_variants, qty)
        high = max(*candidate.qty_variants, qty)
        return high / low <= self.max_qty_ratio

    def _interval_fits(self, candidate: Candidate, interval: float) -> bool:
        if interval < self.min_interval or interval > self.max_interval:
            return False
        if self.interval_tolerance is None or candidate.last_interval is None:
            return True
        low = candidate.last_interval * (1 - self.interval_tolerance)
        high = candidate.last_interval * (1 + self.interval_tolerance)
        return low <= interval <= high

    def _find_match(self, side: str, qty: int, ts: float) -> Candidate | None:
        if self.ignore_qty:
            # TWAP: объём не участвует — ищем любую активную серию этой
            # стороны, к которой подходит интервал.
            for candidate in self.active.get(side, []):
                interval = ts - candidate.last_ts
                if self._interval_fits(candidate, interval):
                    return candidate
            return None

        exact_candidates = self.index.get(side, {}).get(qty, [])
        for candidate in exact_candidates:
            interval = ts - candidate.last_ts
            if self._interval_fits(candidate, interval):
                return candidate

        for candidate in self.active.get(side, []):
            if qty in candidate.qty_variants:
                continue
            if len(candidate.qty_variants) >= self.max_qty_variants:
                continue
            interval = ts - candidate.last_ts
            if not self._interval_fits(candidate, interval):
                continue
            if not self._qty_fits_loose(candidate, qty):
                continue
            return candidate

        return None

    def on_trade(self, trade: dict) -> list[Signal]:
        qty = trade["qty"]
        if qty < self.min_qty:
            return []

        side = trade["side"]
        ts = trade["timestamp"] / 1000.0

        signals = self._prune_dead(side, ts)

        match = self._find_match(side, qty, ts)

        if match is not None:
            interval = ts - match.last_ts
            match.intervals.append(interval)
            match.last_interval = interval
            if qty not in match.qty_variants:
                match.qty_variants.add(qty)
                self._index_new_variant(side, match, qty)
            match.count += 1
            match.last_ts = ts
            match.warned = False  # пришёл вовремя — сбрасываем прежнюю просрочку
            if match.count >= self.MAX_SERIES_LENGTH:
                signals.append(self._finalize(side, match))
                self._unregister(side, match)
        else:
            new_candidate = Candidate(qty, ts)
            self._register(side, new_candidate)

        signals.extend(self._enforce_cap(side))
        return signals

    def check_overdue(self, now_ts: float) -> tuple[list[Signal], list[str]]:
        """Проверка по таймеру (watchdog в живом режиме). Предупреждает
        о просрочке при первом пропуске ожидаемого интервала, но удаляет
        серию только после CLOSE_AFTER_MISSES пропущенных max_interval."""
        signals: list[Signal] = []
        warnings: list[str] = []
        close_threshold = self.max_interval * self.CLOSE_AFTER_MISSES

        for side, candidates in list(self.active.items()):
            for candidate in list(candidates):
                gap = now_ts - candidate.last_ts

                if gap > close_threshold:
                    if candidate.count >= self.min_repeats:
                        signals.append(self._finalize(side, candidate))
                    self._unregister(side, candidate)
                    continue

                # Предупреждаем, только если серия уже дозрела до min_repeats.
                if candidate.count < self.min_repeats:
                    continue

                if self.interval_tolerance is not None and candidate.last_interval is not None:
                    max_fit_interval = candidate.last_interval * (1 + self.interval_tolerance)
                    if gap > max_fit_interval and not candidate.warned:
                        candidate.warned = True
                        qty_str = "-".join(str(q) for q in sorted(candidate.qty_variants))
                        expected_next = candidate.last_ts + candidate.last_interval
                        expected_str = datetime.fromtimestamp(
                            expected_next, tz=timezone.utc
                        ).strftime("%H:%M:%S")
                        overdue_by = gap - candidate.last_interval
                        warnings.append(
                            f"[{self.name}] {self.symbol} {side} qty={qty_str} "
                            f"повторов={candidate.count} просрочка {overdue_by:.1f}с "
                            f"(ожидался удар в {expected_str} UTC)"
                        )

        return signals, warnings

    def get_active_snapshot(self, now_ts: float) -> list[dict]:
        """Текущий срез активных серий — для живой таблицы (dashboard).
        Только читает состояние, ничего не меняет."""
        rows: list[dict] = []
        for side, candidates in self.active.items():
            for candidate in candidates:
                if candidate.count < 2:
                    continue

                if candidate.last_interval is not None:
                    expected_next_ts = candidate.last_ts + candidate.last_interval
                    seconds_to_next = expected_next_ts - now_ts
                else:
                    seconds_to_next = None

                rows.append({
                    "symbol": self.symbol,
                    "preset": self.preset_name,
                    "side": side,
                    "qty_variants": sorted(candidate.qty_variants),
                    "interval": candidate.last_interval,
                    "repeats": candidate.count,
                    "seconds_to_next": seconds_to_next,
                    "start_ts": candidate.start_ts,
                })
        return rows

    def flush(self) -> list[Signal]:
        signals = []
        for side, candidates in self.active.items():
            for candidate in candidates:
                if candidate.count >= self.min_repeats:
                    signals.append(self._finalize(side, candidate))
        self.active.clear()
        self.index.clear()
        return signals