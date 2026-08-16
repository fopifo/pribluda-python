"""
Приблуда на python — универсальный детектор периодичности ("роботов").
Серия сделок с одинаковой стороной (side), повторяющаяся с определённым
интервалом. Режимы матчинга объёма/интервала задаются пресетом:
- объём: либо ТОЧНОЕ совпадение (max_qty_variants=1), либо
  ЧЕРЕДОВАНИЕ конкретных значений (max_qty_variants=2 — например,
  робот бьёт то 45, то 46 лотов; каждое из значений должно совпасть
  ТОЧНО, диапазона/допуска между ними нет — max_qty_ratio защищает от
  случайного попадания слишком далёкого второго значения в ту же
  серию). Диапазон-допуск (±N лотов вокруг любого значения) в проекте
  сознательно НЕ используется — пробовали смягчить так в ветке
  experiments (qty_tolerance_lots), решили не переносить: диапазон
  ловит "что угодно рядом", а не конкретное чередование, для
  скальпинга это снижает уверенность в сигнале.
- интервал: "strict" — первая пара сделок задаёт "эталонный"
  интервал для конкретной серии, каждая следующая сделка должна
  укладываться в него ± interval_tolerance от ПРЕДЫДУЩЕГО
  фактического интервала. Без запасных/смягчённых путей принятия —
  либо сделка укладывается в допуск, либо начинает новую серию.
- "TWAP" (ignore_qty=True): объём вообще не участвует в матчинге —
  ловит паттерн "крупная заявка, нарезанная по времени на равные
  интервалы, с разным объёмом каждый раз" (подтверждено на реальных
  логах: PLZL, BELU). Вся защита от случайных совпадений тут — на
  точности интервала (interval_tolerance строже, min_repeats выше).
  ОТКАТ BEST-FIT (по результатам A/B-прогона 2026-08-16): best-fit
  (сделка уходит кандидату с минимальным отклонением интервала) дал
  обратный эффект — дубли выросли в 3.8 раза, сигналы в 3.7 раза:
  "справедливое" распределение сделок фрагментирует один поток на
  несколько коротких серий. Возвращён first-fit (первый подходящий
  кандидат), он нечаянно работает как "собиратель" длинных серий.
ФИКС СКОРОСТИ: список active[side] всегда отсортирован по last_ts по
возрастанию (новая серия дописывается в конец, продлённая —
переставляется в конец). Тогда: (а) просроченные серии всегда в голове
списка и _prune_dead снимает их оттуда без полного сканирования;
(б) поиск в матчинге идёт с конца списка и обрывается, как только
интервал старше max_interval — сканируются только "живые" кандидаты,
а не тысячи накопленных. На лентах в десятки тысяч сделок это
ускоряет прогон в разы при том же результате.
Фильтр входа (min_qty) — объём в лотах, низкий пол (не потолок).
ДЖИТТЕР и STABILITY_RATIO: каждый Candidate копит полный список
интервалов между сделками серии. При закрытии серии считаем:
- jitter_ms — стандартное отклонение интервалов, мс
- stability_ratio — доля интервалов, попавших в пределы
  time_window_sec от медианы (если пресет задаёт time_window_sec)
Оба — ТОЛЬКО измерение постфактум, справочные метрики для трейдера/
для последующего анализа. НИ ОДНА из них не влияет на то, продлевается
серия или нет — это сознательное разделение "как решаем" (строгий
допуск интервала) от "как оцениваем качество уже найденного" (эти
метрики).
CLOSE_AFTER_MISSES = 2: серия закрывается не после ПЕРВОГО пропущенного
max_interval, а после ВТОРОГО — даёт роботу шанс пережить единичный
случайный пропуск, не теряя уже накопленную серию.
Для живого режима: on_trade() реагирует на приход сделки. check_overdue()
вызывается по таймеру (watchdog), не привязанному к потоку сделок — но
предупреждение о просрочке использует ТЕ ЖЕ границы допуска, что и
_interval_fits в матчинге, чтобы не "опережать" реальную логику.
Производительность: для strict/loose кандидаты индексируются по объёму
(qty) для быстрого точного совпадения. Для TWAP (ignore_qty=True) объём
не индексируется — поиск по живому окну времени (см. фикс скорости).
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
        self.intervals: list[float] = []  # полная история интервалов серии
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
        # Только для stability_ratio (справочная метрика) — НЕ влияет
        # на матчинг/продление серии.
        self.time_window_sec = settings.get("time_window_sec", 0.0)
        preset_name = settings.get("preset_name")
        self.preset_name = preset_name or ""
        if preset_name:
            self.name = f"робот-интервал[{preset_name}]"
        # active[side] — список Candidate, ВСЕГДА отсортирован по
        # last_ts по возрастанию (см. докстринг модуля, ФИКС СКОРОСТИ).
        self.active: dict[str, list[Candidate]] = {}
        self.index: dict[str, dict[int, list[Candidate]]] = {}

    def _finalize(self, side: str, candidate: Candidate) -> Signal:
        avg_interval = (candidate.last_ts - candidate.start_ts) / max(candidate.count - 1, 1)
        jitter_ms = None
        if len(candidate.intervals) >= 2:
            jitter_ms = statistics.pstdev(candidate.intervals) * 1000
        stability_ratio = None
        if len(candidate.intervals) >= 2 and self.time_window_sec > 0:
            median_interval = statistics.median(candidate.intervals)
            good = sum(
                1 for iv in candidate.intervals
                if abs(iv - median_interval) <= self.time_window_sec
            )
            stability_ratio = good / len(candidate.intervals)
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
            stability_ratio=stability_ratio,
        )

    def _register(self, side: str, candidate: Candidate) -> None:
        # last_ts новой серии = текущее время сделки (максимум в списке),
        # поэтому дописывание в конец сохраняет сортировку по last_ts.
        self.active.setdefault(side, []).append(candidate)
        if self.ignore_qty:
            return
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
        интервалов max_interval (ждём второй пропуск, а не первый).
        Список отсортирован по last_ts, просроченные — всегда в голове,
        снимаем их оттуда без полного сканирования."""
        signals: list[Signal] = []
        close_threshold = self.max_interval * self.CLOSE_AFTER_MISSES
        active_list = self.active.get(side, [])
        while active_list and now_ts - active_list[0].last_ts > close_threshold:
            candidate = active_list.pop(0)
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
            # ОТКАТ best-fit: first-fit (первый подходящий), но с
            # фиксом скорости — идём с конца списка (самые свежие
            # last_ts) и обрываемся, как только интервал старше
            # max_interval: старше по списку только дальше, они
            # не подойдут. First-fit по результатам A/B собирает
            # длинные серии лучше, чем best-fit.
            for candidate in reversed(self.active.get(side, [])):
                interval = ts - candidate.last_ts
                if interval > self.max_interval:
                    break
                if self._interval_fits(candidate, interval):
                    return candidate
            return None

        exact_candidates = self.index.get(side, {}).get(qty, [])
        for candidate in exact_candidates:
            interval = ts - candidate.last_ts
            if self._interval_fits(candidate, interval):
                return candidate
        for candidate in reversed(self.active.get(side, [])):
            interval = ts - candidate.last_ts
            if interval > self.max_interval:
                break
            if qty in candidate.qty_variants:
                continue
            if len(candidate.qty_variants) >= self.max_qty_variants:
                continue
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
            match.warned = False
            # Продлённая серия получает last_ts = текущее время —
            # переставляем в конец, сохраняя сортировку списка.
            active_list = self.active.get(side, [])
            if active_list and active_list[-1] is not match:
                active_list.remove(match)
                active_list.append(match)
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
        signals = []
        warnings = []
        close_threshold = self.max_interval * self.CLOSE_AFTER_MISSES
        for side, candidates in list(self.active.items()):
            for candidate in list(candidates):
                gap = now_ts - candidate.last_ts
                if gap > close_threshold:
                    if candidate.count >= self.min_repeats:
                        signals.append(self._finalize(side, candidate))
                    self._unregister(side, candidate)
                    continue
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
        """Текущий срез активных серий — для живой таблицы (dashboard)."""
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
                if len(candidate.intervals) >= 2:
                    jitter_ms = statistics.pstdev(candidate.intervals) * 1000
                else:
                    jitter_ms = None
                rows.append({
                    "symbol": self.symbol,
                    "preset": self.preset_name,
                    "side": side,
                    "qty_variants": sorted(candidate.qty_variants),
                    "interval": candidate.last_interval,
                    "repeats": candidate.count,
                    "seconds_to_next": seconds_to_next,
                    "start_ts": candidate.start_ts,
                    "jitter_ms": jitter_ms,
                })
        return rows

    def flush(self) -> list[Signal]:
        signals = []
        for side, candidates in list(self.active.items()):
            for candidate in candidates:
                if candidate.count >= self.min_repeats:
                    signals.append(self._finalize(side, candidate))
        self.active.clear()
        self.index.clear()
        return signals