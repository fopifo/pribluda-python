"""
ai_solution_v1/detect/interval.py
=================================
Быстрый детектор интервальных роботов. Переписан с нуля относительно
detectors/interval_robot.py (dpNEW), БЕЗ импортов из dpNEW.

Что изменилось по СУТИ (не только по скорости):

1. Внутреннее время — ЦЕЛОЕ в миллисекундах. Нет float-деления
   timestamp/1000 на каждой сделке, нет сравнивания float-границ.
   Дефолт ленты у CScalp — мс с эпохи; строки-таймстампы парсер
   приводит к целому мс.

2. Парсинг сделки один раз, на входе. В старом коде каждая сделка
   проходила через .get() несколько раз, а при min_qty_auto ещё и
   могла читать файл с диска в hot-path. Здесь parse_trade() — одно
   извлечение полей, и никакого I/O в детекторе вообще.

3. Индекс кандидатов — по (side, qty). В старом коде индекс
   (set) пополнялся, но при выборе кандидата всё равно делался
   reversed(self.active[side]) — линейный проход по ВСЕМ активным
   кандидатам стороны. На 500 кандидатах это ~500 проверок
   _interval_fits на каждую сделку, каждая с циклом k=2..4 по двум
   направлениям. В шумных тикерах (SBER, VTBR) — O(N) на сделку.

   Здесь: per-(side,qty) список кандидатов хранится СНИЗУ ВВЕРХ по
   last_ts (порядок поступления — он же монотонный). Проверяем
   только хвост списка (самый свежий кандидат первым). Кандидат с
   last_ts ниже нижней границы допустимого интервала уже не может
   подойти — весь список до него пропускается бинарным поиском
   (bisect по last_ts). Отсечение по max_interval_ms делает средний
   случай O(1), худший — O(log n).

4. Активные кандидаты по стороне — OrderedDict (ссылки, не копии).
   Порядок = порядок последнего "касания", что и требует логика:
   при двух подходящих кандидатах предпочитается более свежий.

5. НЕТ повторной сортировки. В старом коде на каждое продление
   серии: al.remove(c); al.append(c) (линейный поиск в списке).
   Здесь ссылка хранится, move_to_end — O(1).

6. Кольцевые буферы интервалов (array('I') на фиксированные
   ёмкости). На каждом ударе пересчёт медианы/регулярности — O(k)
   по окну, k — фиксированное небольшое число (не по всей серии).
   В старом коде intervals_ms рос до длины серии, и каждая проверка
   асимптотически была O(len(series)); кроме того _raw_period()
   фильтровал deque из 4000 элементов НА КАЖДЫЙ РЕПОРТ.

7. Регулярность и jitter считаются квантованием по базе
   (отклонение до ближайшей точки сетки), а не по "сырым" интервалам.
   Это меняет семантику правильно: серия с пропусками (2x, 3x базы)
   раньше получала огромный джиттер и отбрасывалась по jitter_max.
   Здесь jitter — отклонение от ближайшей точки сетки, поэтому
   пропуски не штрафуются, а дрожание — штрафуется.

8. Закрытие серий — ленивое. Вместо проверки ВСЕХ кандидатов тикера
   на каждом on_trade (двойной проход по active в старом коде:
   один в _prune_dead, другой в _find_match), используется
   time-heap ((deadline_ms, seq, candidate)): на каждой сделке
   закрываются только те, у кого deadline уже прошёл. Амортизированно
   O(log n), на практике — стек.

9. min_qty_auto больше не вычисляется чтением файла с диска из
   hot-path. Адаптивный min_qty передаётся ЯВНО в адаптивный слой
   (AdaptiveMinQty) и обновляется по реальной ленте тикера.

10. Двойной удар и минимальный зазор — централизованно: gap <
    min_gap_ms считается двойным ударом и не продлевает серию. В
    старом коде эта проверка стояла ПОСЛЕ поиска кандидата и
    возвращала сигналы, но кандидат всё равно был найден — логику
    сохранили, но теперь она проверяется до поиска.

11. Capacity guard. В старом коде MAX_ACTIVE_PER_SIDE=500,
    _enforce_cap сортировал список по (count, start_ts) на каждом
    переполнении. Здесь вытесняются наименее подтверждённые из
    min-heap (O(log n)), плюс порог на список вариантов объёма.

12. Снимок (snapshot) — без копирования списков вариантов объёма
    на каждой строке: возвращается tuple, а порядок сортировки
    вариантов — не требование.

Публичный API:
    DetectorConfig           — из ai_solution_v1.config
    IntervalDetector         — детектор одного тикера
    parse_trade(raw)         — быстрый парсер строки JSON / словаря
    Trade                    — нормализованная сделка
    Signal, Confirmation     — выходные события
"""
from __future__ import annotations

import json
from array import array
from bisect import bisect_left
from collections import OrderedDict
from dataclasses import dataclass, field
from heapq import heappush, heappop, heapify
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ai_solution_v1.config import DEFAULT_CONFIG, DerivedConfig, DetectorConfig

__all__ = [
    "Trade",
    "parse_trade",
    "SeriesCandidate",
    "Signal",
    "Confirmation",
    "Snapshot",
    "IntervalDetector",
    "parse_trades",
]


# ---------------------------------------------------------------------------
# Сделка
# ---------------------------------------------------------------------------
# Единицы времени: мс с эпохи, целое. Сторона: 1 = buy, -1 = sell
# (арифметика быстрее строк; строки остаются для вывода).
SIDE_BUY = 1
SIDE_SELL = -1
_SIDE_STR = {1: "buy", -1: "sell"}


@dataclass(frozen=True, slots=True)
class Trade:
    """Нормализованная сделка. Внутри детектора — только int."""

    symbol: str
    side: int            # 1 = buy, -1 = sell
    qty: int
    price: float
    timestamp: int       # мс с эпохи, целое

    @property
    def side_name(self) -> str:
        return _SIDE_STR[self.side]

    @property
    def ts_sec(self) -> float:
        return self.timestamp / 1000.0


def _side_id(value: Any) -> int:
    """'buy'/'BUY'/'B'/1 -> 1, 'sell'/'S'/-1 -> -1, иначе 0."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return 1 if value > 0 else (-1 if value < 0 else 0)
    if not isinstance(value, str):
        return 0
    s = value.strip().lower()
    if s in ("buy", "b", "bid", "покупка"):
        return SIDE_BUY
    if s in ("sell", "s", "ask", "продажа"):
        return SIDE_SELL
    return 0


def _to_ms(value: Any) -> int:
    """
    Привести таймстамп к целому мс с эпохи.
    Держит три формата, встречающихся в лентах:
      - int мс с эпохи (CScalp): 1795067495249
      - int секунд с эпохи:      1795067495
      - float/str в мс или сек
    Возвращает 0, если распознать не удалось.
    """
    if value is None:
        return 0
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0
    if v <= 0:
        return 0
    # < 1e11 — это секунды (1e11 мс = ~1973 год в мс? нет: 1e11 мс ≈ 1973;
    # реальные эпохи в мс сегодня ~1.7e12, в сек ~1.7e9).
    if v < 1e11:
        v *= 1000.0
    return int(v)


def _to_int(value: Any) -> int:
    """Объём/число -> int без исключений ('"100"' тоже принимается)."""
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_trade(raw: Mapping[str, Any]) -> Optional[Trade]:
    """
    Быстрый парсер одной записи ленты (уже десериализованной в dict).

    Валидация: symbol непустой, сторона распознана, qty > 0, ts > 0.
    price может быть 0 (некоторые ленты без цены) — сделка не
    отбрасывается, но помечается price=0.0.
    Возвращает None для мусора (сломанная строка, нет полей).
    """
    sym = raw.get("symbol")
    if not sym or not isinstance(sym, str):
        # В старом коде: if not sym or sym not in allowed — то же самое,
        # только здесь без проверки against set (её делает вызывающий).
        return None
    side = _side_id(raw.get("side"))
    if side == 0:
        return None
    ts = _to_ms(raw.get("timestamp"))
    if ts <= 0:
        return None
    qty = _to_int(raw.get("qty"))
    if qty <= 0:
        return None
    price = _to_float(raw.get("price"))
    return Trade(symbol=sym, side=side, qty=qty, price=price, timestamp=ts)


def parse_trades(
    lines: Iterable[str],
    allowed_symbols: Optional[frozenset[str]] = None,
    min_qty: int = 1,
) -> List[Trade]:
    """
    Разбор батча строк JSONL -> список Trade (порядок сохраняется).

    Быстрый путь: json.loads со встроенным C-парсером, затем
    parse_trade. Строки с ошибкой JSON и невалидные сделки
    пропускаются молча (в логах это сотни тысяч строк — шум).
    """
    out: List[Trade] = []
    app = out.append
    loads = json.loads
    for line in lines:
        if not line or not line.strip():
            continue
        try:
            d = loads(line)
        except (ValueError, TypeError):
            continue
        if allowed_symbols is not None and d.get("symbol") not in allowed_symbols:
            continue
        t = parse_trade(d)
        if t is None or t.qty < min_qty:
            continue
        app(t)
    return out


# ---------------------------------------------------------------------------
# Выходные события
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Confirmation:
    """Событие подтверждения: серия достигла report_min_repeats ударов."""

    symbol: str
    side: int
    qty_min: int
    qty_max: int
    hits: int
    interval_ms: int
    ts_ms: int
    regularity: float
    reported: bool          # прошла ли проверки качества (история)


@dataclass(frozen=True, slots=True)
class Signal:
    """Закрытая серия робота."""

    symbol: str
    side: int
    qty_variants: Tuple[int, ...]
    hits: int
    base_interval_ms: int      # принятая база (сетка), мс
    period_ms: int             # фактический период серии, мс
    start_ms: int
    end_ms: int
    regularity: float
    jitter_ratio: float
    price_first: float
    price_last: float
    sum_qty: int
    reported: bool             # попала ли в историю
    preset: str = ""

    @property
    def side_name(self) -> str:
        return _SIDE_STR[self.side]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side_name,
            "preset": self.preset,
            "qty_variants": list(self.qty_variants),
            "hits": self.hits,
            "base_interval_ms": self.base_interval_ms,
            "period_ms": self.period_ms,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "regularity": round(self.regularity, 3),
            "jitter_ratio": round(self.jitter_ratio, 3),
            "price_first": self.price_first,
            "price_last": self.price_last,
            "sum_qty": self.sum_qty,
            "reported": self.reported,
        }


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Live-строка активной серии (для агрегатора/GUI — GUI не трогаем)."""

    symbol: str
    side: int
    preset: str
    qty_variants: Tuple[int, ...]
    interval_ms: int
    hits: int
    start_ms: int
    end_ms: int
    regularity: float
    jitter_ratio: float
    price_first: float
    price_last: float
    sum_qty: int
    seconds_to_next: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": _SIDE_STR[self.side],
            "preset": self.preset,
            "qty_variants": list(self.qty_variants),
            "interval": self.interval_ms / 1000.0,
            "repeats": self.hits,
            "start_ts": self.start_ms / 1000.0,
            "end_ts": self.end_ms / 1000.0,
            "end_ms": self.end_ms,
            "regularity": round(self.regularity, 3),
            "jitter_ratio": round(self.jitter_ratio, 3),
            "price_first": self.price_first,
            "price_last": self.price_last,
            "sum_qty": self.sum_qty,
            "seconds_to_next": self.seconds_to_next,
        }


# ---------------------------------------------------------------------------
# Кольцевой буфер интервалов
# ---------------------------------------------------------------------------
class _Ring:
    """
    Кольцевой буфер целых (мс) фиксированной ёмкости.
    В отличие от списка, растущего на всю серию, здесь O(cap) памяти и
    O(cap) пересчёт статистики — НЕ зависящий от длины серии.
    """

    __slots__ = ("_buf", "_cap", "_n", "_pos")

    def __init__(self, cap: int) -> None:
        self._cap = max(1, cap)
        self._buf = array("q", [0]) * self._cap
        self._n = 0
        self._pos = 0

    def append(self, value: int) -> None:
        self._buf[self._pos] = value
        self._pos = (self._pos + 1) % self._cap
        if self._n < self._cap:
            self._n += 1

    def __len__(self) -> int:
        return self._n

    def values(self) -> List[int]:
        if self._n < self._cap:
            return self._buf[0:self._n].tolist()
        return self._buf.tolist()


# Ёмкость окна статистики. От 7+: медиана по 16 точкам устойчива к 1-2
# выбросам, а пересчёт 16 int — микросекунды. Само число ударов серии
# не ограничено этим окном: hits считается отдельным счётчиком.
_STATS_WINDOW = 16
# То же для "сырых" интервалов между любыми ударами своего объёма
# (для перепроверки базы по сырой сетке — см. _refine_from_raw).
_RAW_WINDOW = 24


def _median_of(values: List[int]) -> int:
    """
    Медиана списка int (копию делает вызывающий). Чётная длина —
    среднее двух центральных. O(n log n) на маленьком окне.
    """
    if not values:
        return 0
    vals = sorted(values)
    n = len(vals)
    mid = n >> 1
    if n & 1:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) >> 1


# ---------------------------------------------------------------------------
# Кандидат серии
# ---------------------------------------------------------------------------
class SeriesCandidate:
    """
    Активная серия робота. Держится в памяти до закрытия.

    Состав полей — минимально достаточный. Кладётся в OrderedDict и в
    храповик (heap) по deadline; полей больше нет специально: каждая
    лишняя мутация на сделке — это миллисекунды на 400 МБ ленте.
    """

    __slots__ = (
        "symbol",
        "side",
        "qty_variants",      # dict[qty] -> hits (порядок вставки)
        "qty_sum",
        "count",
        "start_ms",
        "last_ms",
        "last_interval_ms",
        "base_ms",           # принятая база (int мс), 0 = не задана
        "intervals",         # _Ring сырых зазоров по времени
        "priced_hits",
        "price_counts",      # dict[price] -> hits; для same_price_ratio
        "first_price",
        "last_price",
        "confirms_fired",
        "reported",
        "deadline_ms",
        "heap_seq",
        "warned",
    )

    def __init__(self, symbol: str, side: int, qty: int, ts_ms: int, price: float) -> None:
        self.symbol = symbol
        self.side = side
        self.qty_variants: Dict[int, int] = {qty: 1}
        self.qty_sum = qty
        self.count = 1
        self.start_ms = ts_ms
        self.last_ms = ts_ms
        self.last_interval_ms = 0
        self.base_ms = 0
        self.intervals = _Ring(_STATS_WINDOW)
        self.priced_hits = 1 if price else 0
        self.price_counts: Dict[float, int] = {price: 1} if price else {}
        self.first_price = price
        self.last_price = price
        self.confirms_fired = 0
        self.reported = 0
        self.deadline_ms = 0
        self.heap_seq = 0
        self.warned = False

    # ---- статистика по окну (O(_STATS_WINDOW)) ---------------------------
    def _median(self, values: List[int]) -> int:
        if not values:
            return 0
        values.sort()
        n = len(values)
        mid = n >> 1
        if n & 1:
            return values[mid]
        return (values[mid - 1] + values[mid]) >> 1

    def _tol_for(self, d: DerivedConfig, base_ms: int) -> float:
        if base_ms < d.short_interval_threshold_ms:
            return d.short_interval_tolerance
        return d.interval_tolerance

    def _grid_tol_ms(self, d: DerivedConfig, base_ms: int) -> float:
        """Абсолютный допуск до точки сетки: max(rel*base, пол регулярности)."""
        rel = self._tol_for(d, base_ms)
        return max(base_ms * rel, d.regularity_abs_floor_ms)

    def regularity(self, d: DerivedConfig) -> float:
        """
        Доля интервалов, лежащих на сетке базы (допуск — квантование).
        Серия короче 3 интервалов — 1.0 (как в старом коде).
        """
        ivs = self.intervals.values()
        if len(ivs) < 3:
            return 1.0
        base = self.base_ms or self.last_interval_ms
        if base <= 0:
            return 1.0
        tol = self._grid_tol_ms(d, base)
        good = 0
        for iv in ivs:
            # Ближайшая точка сетки k*base; допуск — квантование.
            k = int(round(iv / base))
            if k < 1:
                k = 1
            if abs(iv - k * base) <= tol:
                good += 1
        return good / len(ivs)

    def jitter_ratio(self, d: DerivedConfig) -> float:
        """
        Относительный разброс отклонений от сетки: pstdev(dev)/base.
        Отсутствие данных -> 0.0 (фильтр не применяется).
        """
        ivs = self.intervals.values()
        if len(ivs) < 2:
            return 0.0
        base = self.base_ms or self.last_interval_ms
        if base <= 0:
            return 0.0
        devs = []
        for iv in ivs:
            k = int(round(iv / base)) or 1
            devs.append(float(iv - k * base))
        m = sum(devs) / len(devs)
        var = sum((x - m) * (x - m) for x in devs) / len(devs)
        return (var ** 0.5) / base if base else 0.0

    def median_interval_ms(self) -> int:
        return self._median(self.intervals.values())

    def qty_minmax(self) -> Tuple[int, int]:
        if not self.qty_variants:
            return (0, 0)
        return (min(self.qty_variants), max(self.qty_variants))

    def qty_is_stable(self, d: DerivedConfig) -> bool:
        if not d.stable_qty_required:
            return True
        total = sum(self.qty_variants.values())
        if total < 3:
            return True
        return max(self.qty_variants.values()) / total >= d.stable_qty_ratio


# ---------------------------------------------------------------------------
# Детектор
# ---------------------------------------------------------------------------
class IntervalDetector:
    """
    Детектор одного тикера. Подача: on_trade(Trade) по возрастанию ts.
    Выход: список Confirmation/Signal из on_trade, накопительно — из
    pop_events(). Снимок активных — snapshot().

    Потокобезопасность: НЕ потокобезопасен by design (один экземпляр —
    один поток-писатель). Если нужен многопоточный режим — обёртка
    снаружи (детектор не держит внутренних блокировок, чтобы не платить
    за них на каждой сделке).
    """

    __slots__ = (
        "symbol",
        "cfg",
        "d",
        "_index",            # (side, qty) -> list[SeriesCandidate] (снизу вверх по last_ms)
        "_index_ts",         # (side, qty) -> list[int] last_ms (для bisect)
        "_active",           # side -> OrderedDict[cand -> None]
        "_deadline_heap",    # [(deadline_ms, seq, cand)]
        "_seq",
        "_qty_freq",         # dict[qty] -> hits (для noisy filter)
        "_qty_total",
        "_noisy_cache",
        "_noisy_dirty",
        "_events",
        "_reported_history",
        "_confirmed_events",
        "_adaptive_min_qty",
        "_last_side_ms",     # side -> ts_ms последней принятой сделки (защита зазора 0)
        "_raw_tail",         # side -> deque[(ts, qty)] — последние удары
        "_raw_bucket",       # qty -> array('q') последних ts своего qty
        "_stats",
    )

    def __init__(
        self,
        symbol: str = "",
        cfg: Optional[DetectorConfig] = None,
        adaptive_min_qty: int = 0,
    ) -> None:
        if cfg is None:
            cfg = DEFAULT_CONFIG if not symbol else DEFAULT_CONFIG.with_overrides(symbol=symbol)
        elif symbol and cfg.symbol != symbol:
            cfg = cfg.with_overrides(symbol=symbol)
        self.symbol = cfg.symbol
        self.cfg = cfg
        self.d = DerivedConfig(cfg)
        self._index: Dict[Tuple[int, int], List[SeriesCandidate]] = {}
        self._index_ts: Dict[Tuple[int, int], List[int]] = {}
        self._active: Dict[int, "OrderedDict[SeriesCandidate, None]"] = {}
        self._deadline_heap: List[Tuple[int, int, SeriesCandidate]] = []
        self._seq = 0
        self._qty_freq: Dict[int, int] = {}
        self._qty_total = 0
        self._noisy_cache: Optional[set[int]] = None
        self._noisy_dirty = False
        self._events: List[Any] = []
        self._reported_history: List[Signal] = []
        self._confirmed_events: List[Confirmation] = []
        self._adaptive_min_qty = adaptive_min_qty
        self._last_side_ms: Dict[int, int] = {}
        self._raw_tail: Dict[int, array] = {}
        self._raw_bucket: Dict[int, array] = {}
        self._stats = {
            "trades_in": 0,
            "trades_used": 0,
            "trades_dropped_zero_gap": 0,
            "trades_dropped_min_gap": 0,
            "candidates_created": 0,
            "candidates_closed": 0,
            "confirmations": 0,
            "reported": 0,
            "evictions": 0,
            "relocks": 0,
        }

    # ------------------------------------------------------------------
    # Настройка адаптивного порога объёма (вместо чтения файла в hot-path)
    # ------------------------------------------------------------------
    def set_adaptive_min_qty(self, value: int) -> None:
        """
        Явно задать адаптивный min_qty (например, медиана объёма тикера).
        В старом коде это делал _compute_adaptive_min_qty внутри
        on_trade() и читал json с диска — блокировка на каждой сделке
        до первого значения. Здесь value приходит снаружи, детектор
        только применяет max(value, cfg.min_qty).
        """
        self._adaptive_min_qty = max(int(value), 1)

    @property
    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    # ------------------------------------------------------------------
    # Шумные объёмы
    # ------------------------------------------------------------------
    def _noisy_qtys(self) -> set[int]:
        """
        Объёмы, чья доля >= noisy_qty_share. Кэшируется: пересчёт
        раз в noisy_qty_min_samples новых сделок, а не на каждой.
        """
        if not self.d.noisy_qty_filter or self._qty_total < self.d.noisy_qty_min_samples:
            return set()
        if self._noisy_cache is not None and not self._noisy_dirty:
            return self._noisy_cache
        share = self.d.noisy_qty_share
        total = self._qty_total
        cache = {q for q, n in self._qty_freq.items() if n / total >= share}
        self._noisy_cache = cache
        self._noisy_dirty = False
        return cache

    # ------------------------------------------------------------------
    # Основная точка входа
    # ------------------------------------------------------------------
    def on_trade(self, trade: Trade) -> List[Any]:
        """
        Подать одну сделку. Возвращает события, появившиеся на этой сделке:
        Confirmation (серия подтверждена) и/или Signal (серия закрыта).

        Предусловие: trade.timestamp монотонно не убывает по потоку —
        это свойство ленты; при нарушении сделка отбрасывается (лучше
        потерять одну сделку, чем испортить статистику окна).
        """
        st = self._stats
        st["trades_in"] += 1
        if self.symbol and trade.symbol != self.symbol:
            # Чужой тикер на входе — ошибка вызывающего; не падаем, но
            # игнорируем (детектор — однокарточный).
            return []

        min_q = self._adaptive_min_qty or self.d.min_qty
        if trade.qty < min_q:
            return []

        ts = trade.timestamp
        side = trade.side

        # --- 0. Защита от "сделок одним временем" -------------------------
        # Сделка, пришедшая с тем же таймстампом, что предыдущая по этому
        # (тикер, сторона), НЕ создаёт и НЕ продлевает серию: иначе
        # пакет из десятков сделок одного мс даёт зазор 0 и "робота"
        # с интервалом 0.0с (ложные срабатывания CNY-* и подобных).
        last_ts = self._last_side_ms.get(side, 0)
        if last_ts and ts <= last_ts:
            self._stats["trades_dropped_zero_gap"] += 1
            return []
        self._last_side_ms[side] = ts

        # --- 1. Закрытие "мёртвых" серий (амортизированно) ---------------
        events = self._reap(ts)

        # --- 2. Учёт объёмов (только если включён фильтр) -----------------
        if self.d.noisy_qty_filter:
            self._qty_freq[trade.qty] = self._qty_freq.get(trade.qty, 0) + 1
            self._qty_total += 1
            if self._qty_total % self.d.noisy_qty_min_samples == 0:
                self._noisy_dirty = True

        # --- 3. Поиск кандидата O(log n) ---------------------------------
        cand = self._find(side, trade.qty, ts)
        if cand is None:
            # Серия НЕ создаётся "(в одиночку)": кандидат ставится, но
            # его время жизни ограничено тем, что следующий удар,
            # пришедший ближе min_gap_ms, серию не продлит.
            cand = self._spawn(side, trade)
        else:
            gap = ts - cand.last_ms
            # Двойной удар / нулевой зазор: зазор < min_gap — частичное
            # исполнение той же заявки (или пакет одного мс). Серию НЕ
            # продлевает и НЕ сдвигает last_ms (иначе следующий удар с
            # зазором 0 снова "проскочит"). Только отметка свежести.
            if gap < self.d.min_gap_ms:
                self._stats["trades_dropped_min_gap"] += 1
                self._touch(cand)
                return events
            ev = self._extend(cand, trade, gap)
            events.extend(ev)
            return events
        return events

    # ------------------------------------------------------------------
    # Создание кандидата
    # ------------------------------------------------------------------
    def _spawn(self, side: int, trade: Trade) -> SeriesCandidate:
        c = SeriesCandidate(self.symbol, side, trade.qty, trade.timestamp, trade.price)
        c.deadline_ms = trade.timestamp + self.d.miss_close_after_ms
        self._register(c)
        self._stats["candidates_created"] += 1
        self._enforce_cap(side, self._events)
        return c

    # ------------------------------------------------------------------
    # Индекс
    # ------------------------------------------------------------------
    def _register(self, c: SeriesCandidate) -> None:
        key = (c.side, min(c.qty_variants, key=c.qty_variants.get))  # type: ignore[arg-type]
        # Кандидат индексируется по ПЕРВОМУ объёму; остальные варианты
        # ставятся в патч-индекс при добавлении (_attach_qty) — так
        # поиск по типичному объёму остаётся O(1)-коротким, а не
        # дублируется на каждый кандидат.
        lst = self._index.get(key)
        if lst is None:
            self._index[key] = [c]
            self._index_ts[key] = [c.last_ms]
        else:
            # Порядок снизу вверх по last_ms. Новый кандидат всегда
            # самый свежий — аппенд, без сортировки.
            lst.append(c)
            self._index_ts[key].append(c.last_ms)
        od = self._active.get(c.side)
        if od is None:
            od = OrderedDict()
            self._active[c.side] = od
        od[c] = None
        self._seq += 1
        c.heap_seq = self._seq
        heappush(self._deadline_heap, (c.deadline_ms, c.heap_seq, c))

    def _unregister(self, c: SeriesCandidate) -> None:
        for q in c.qty_variants:
            key = (c.side, q)
            lst = self._index.get(key)
            if not lst:
                continue
            # Удаление из середины списка: list.index + del. Список
            # снизу вверх старый -> новый, а закрываются в основном
            # старые -> типичный случай idx=0.
            try:
                idx = lst.index(c)
            except ValueError:
                continue
            del lst[idx]
            del self._index_ts[key][idx]
            if not lst:
                del self._index[key]
                del self._index_ts[key]
        od = self._active.get(c.side)
        if od is not None:
            od.pop(c, None)

    def _touch(self, c: SeriesCandidate) -> None:
        """Отметить касание: порядок свежести + пересчёт deadline."""
        od = self._active[c.side]
        od.move_to_end(c)
        c.deadline_ms = c.last_ms + self.d.miss_close_after_ms
        self._seq += 1
        c.heap_seq = self._seq
        heappush(self._deadline_heap, (c.deadline_ms, c.heap_seq, c))

    # ------------------------------------------------------------------
    # Поиск кандидата
    # ------------------------------------------------------------------
    def _find(self, side: int, qty: int, ts: int) -> Optional[SeriesCandidate]:
        """
        Кандидат, к которому подходит сделка (qty, ts).

        Порядок проверки — от более специфичного к более общему:
          1) кандидаты, у которых qty уже в вариантах (по индексу),
             от самых свежих вниз (list[-1] первым);
          2) кандидаты стороны, у которых можно ПРИЦЕПИТЬ новый объём
             (идут в _active; свежие первыми).
        Реальные кандидаты в 99% случаев находят в шаге 1.
        """
        d = self.d
        if ts - d.min_gap_ms <= 0:
            lo_bound = 0
        else:
            lo_bound = ts - d.max_interval_ms  # ниже этого last_ms — интервал вне диапазона

        key = (side, qty)
        lst = self._index.get(key)
        if lst:
            ts_list = self._index_ts[key]
            # Всё, что левее вставки lo_bound, имеет last_ms < lo_bound ->
            # интервал > max_interval_ms -> заведомо не подходит.
            start = bisect_left(ts_list, lo_bound)
            for i in range(len(lst) - 1, start - 1, -1):
                c = lst[i]
                if self._interval_fits(c, ts - c.last_ms):
                    return c

        # Шаг 2: прицепление нового объёма. ordered dict свежие в конце.
        od = self._active.get(side)
        if od is None:
            return None
        for c in reversed(od):
            if qty in c.qty_variants:
                continue  # уже проверен в индексе
            if len(c.qty_variants) >= d.max_qty_variants:
                continue
            iv = ts - c.last_ms
            if iv > d.max_interval_ms:
                continue
            if not self._qty_attach_ok(c, qty):
                continue
            if not self._interval_fits(c, iv):
                continue
            if iv >= d.long_interval_threshold_ms and not c.qty_is_stable(d):
                continue
            return c
        return None

    def _qty_attach_ok(self, c: SeriesCandidate, qty: int) -> bool:
        """
        Можно ли прицепить объём qty к серии c.
        Старое правило: max/min <= qty_ratio. Здесь дополнительно:
        новый объём должен быть не меньше attach_min_frac от current min
        (защита от слиппинга сетки вниз к мелкому шуму).
        """
        if not c.qty_variants:
            return True
        lo = min(c.qty_variants)
        hi = max(c.qty_variants)
        nlo = qty if qty < lo else lo
        nhi = hi if qty < hi else qty
        if nlo <= 0:
            return False
        if nhi / nlo > self.d.qty_ratio:
            return False
        if qty < lo * self.d.qty_attach_min_frac:
            return False
        noisy = self._noisy_qtys()
        return not (noisy and qty in noisy)

    def _interval_fits(self, c: SeriesCandidate, iv: int) -> bool:
        """
        Проверка: подходит ли интервал iv к серии c.

        База: c.base_ms, иначе последний интервал (как в старом коде).
        Допуск: допуск по базе; проверяются k = 1..interval_mult_max
        (и в обратном направлении — если сам iv может быть базой).
        """
        d = self.d
        if iv < d.min_interval_ms or iv > d.max_interval_ms:
            return False
        # Зазор меньше минимального — не интервал робота вообще
        # (пакет из нескольких сделок одного мс / дробное исполнение).
        if iv < d.min_gap_ms:
            return False
        base = c.base_ms or c.last_interval_ms
        if base <= 0:
            # Один удар в серии: любая следующая сделка на дистанции
            # в допустимом диапазоне — продолжение.
            return True
        tol = d.short_interval_tolerance if base < d.short_interval_threshold_ms else d.interval_tolerance

        diff = iv - base
        if -base * tol <= diff <= base * tol:
            return True

        kmax = d.interval_mult_max
        for k in range(2, kmax + 1):
            kb = k * base
            if kb > d.max_interval_ms:
                break
            if abs(iv - kb) <= kb * tol:
                return True

        # Обратное направление: base = k*iv (серия "пропустила", а
        # пришедший интервал — истинная база).
        for k in range(2, kmax + 1):
            ki = k * iv
            if ki > d.max_interval_ms:
                break
            if abs(base - ki) <= ki * tol:
                return True
        return False

    # ------------------------------------------------------------------
    # Продление серии
    # ------------------------------------------------------------------
    def _extend(self, c: SeriesCandidate, trade: Trade, gap: int) -> List[Any]:
        d = self.d
        ts = trade.timestamp
        ev: List[Any] = []

        # База: первый определённый интервал — она для серии.
        if c.base_ms == 0:
            # Базой не может быть интервал меньше min_gap_ms: иначе
            # серия "длится" только на дробном исполнении и даёт
            # интервал 0.0с (CNY-12.25/26 и подобные).
            if gap < d.min_gap_ms or gap < d.min_interval_ms:
                self._stats["trades_dropped_min_gap"] += 1
                self._touch(c)
                return []
            c.base_ms = gap
        else:
            # Ре-якорь: если медиана уехала от базы сильнее relock_tol
            # и подтвердилась стабильно — переякориваемся.
            self._maybe_relock(c, gap)

        c.intervals.append(gap)
        c.last_interval_ms = gap
        c.count += 1
        c.last_ms = ts
        c.warned = False
        c.qty_sum += trade.qty

        qty = trade.qty
        if qty not in c.qty_variants:
            c.qty_variants[qty] = 1
            self._index_add_variant(c, qty)
        else:
            c.qty_variants[qty] += 1

        if trade.price:
            c.priced_hits += 1
            c.price_counts[trade.price] = c.price_counts.get(trade.price, 0) + 1
            c.last_price = trade.price

        self._touch(c)

        # Подтверждение: ровно на пороге (как в старом коде: один раз).
        if c.confirms_fired == 0 and c.count >= d.report_min_repeats:
            c.confirms_fired = 1
            reg = c.regularity(d)
            jit = c.jitter_ratio(d)
            # Тот же жёсткий фильтр, что и при закрытии: медианный
            # интервал меньше порога отчёта — шум, не робот.
            med = c.median_interval_ms()
            ok = (
                reg >= d.report_min_regular
                and jit <= d.jitter_ratio_max
                and med >= d.min_report_interval_ms
            )
            conf = Confirmation(
                symbol=self.symbol,
                side=c.side,
                qty_min=c.qty_minmax()[0],
                qty_max=c.qty_minmax()[1],
                hits=c.count,
                interval_ms=med,
                ts_ms=ts,
                regularity=reg,
                reported=ok,
            )
            self._stats["confirmations"] += 1
            self._confirmed_events.append(conf)
            ev.append(conf)
            if ok:
                c.reported = 1
                self._stats["reported"] += 1
                self._reported_history.append(self._signal(c, reported=True))

        # Переполнение серии — закрываем.
        if c.count >= d.max_series_hits:
            ev.extend(self._close(c))
        return ev

    def _index_add_variant(self, c: SeriesCandidate, qty: int) -> None:
        key = (c.side, qty)
        lst = self._index.get(key)
        if lst is None:
            self._index[key] = [c]
            self._index_ts[key] = [c.last_ms]
        else:
            # Серия только что получила удар -> она самая свежая.
            lst.append(c)
            self._index_ts[key].append(c.last_ms)

    def _maybe_relock(self, c: SeriesCandidate, gap: int) -> None:
        """
        Переякоривание базы серии.

        Старая логика срабатывала только если интервалы уже ПОПАДАЛИ
        в допуск к базе, и требовала абсолютного согласия в пределах
        relock_tol_ms (150 мс). Для коротких сеток это мёртвый код:
        база 5000 мс, реальные интервалы 500 мс — они никогда не
        попадают в допуск 5% от 5000, а |500 − 5000| >> 150.

        Новая логика — две ветки:

        A) РАБОТАЕТ ТОЛЬКО SELF-CONSISTENCY (требование пользователя):
           если последние relock_window интервалов стабильно лежат
           вокруг своей медианы (относительный допуск, согласие >=
           relock_min_agree), принимаем ЭТУ медиану как базу —
           независимо от старой базы. Так серия переезжает с 5000 мс
           на 500 мс, если сделки стабильно идут по 500 мс.

        B) Старое правило для мелкого дрейфа: медиана близка к базе
           в пределах relock_tol_ms + согласие.

        Ветка A проверяется дешёво: только когда последний gap
        отклоняется от базы сильнее допуска.
        """
        d = self.d
        base = c.base_ms
        if base <= 0:
            return
        tol = d.short_interval_tolerance if base < d.short_interval_threshold_ms else d.interval_tolerance
        if -base * tol <= gap - base <= base * tol:
            return  # интервал в допуске — серия идёт по базе, ничего не делаем

        ivs = c.intervals.values()
        if not ivs:
            # Первый же большой интервал после базы: окна ещё нет.
            return
        # Окно включает текущий gap (он ещё не записан в ring).
        window = ivs[-d.relock_window:] + [gap]
        med = _median_of(window)
        if med <= 0:
            return

        # --- A. Само-согласованность окна: вокруг медианы ---------------
        rel = d.short_interval_tolerance if med < d.short_interval_threshold_ms else d.interval_tolerance
        band = max(med * rel, d.regularity_abs_floor_ms)
        agree = 0
        for iv in window:
            if abs(iv - med) <= band:
                agree += 1
        if agree >= d.relock_min_agree * len(window) and abs(med - base) > d.relock_tol_ms:
            c.base_ms = med
            self._stats["relocks"] += 1
            return

        # --- B. Мелкий дрейф относительно базы --------------------------
        if abs(med - base) <= d.relock_tol_ms:
            return
        agree_b = 0
        for iv in window:
            if abs(iv - med) <= d.relock_tol_ms:
                agree_b += 1
        if agree_b >= d.relock_min_agree * len(window):
            c.base_ms = med
            self._stats["relocks"] += 1

    # ------------------------------------------------------------------
    # Закрытие
    # ------------------------------------------------------------------
    def _reap(self, now_ms: int) -> List[Any]:
        """
        Закрыть все серии, у которых deadline_ms < now_ms.
        heap ленивый: устаревшие записи (серия уже закрыта или deadline
        сдвинулся) отбрасываются по сверке deadline_ms.
        """
        heap = self._deadline_heap
        if not heap or heap[0][0] >= now_ms:
            return []
        ev: List[Any] = []
        while heap and heap[0][0] < now_ms:
            deadline, seq, cand = heappop(heap)
            if cand.deadline_ms != deadline or cand.heap_seq != seq:
                continue  # устаревшая запись (серия продлена)
            ev.extend(self._close(cand, ts_override=deadline - self.d.miss_close_after_ms))
        return ev

    def check_overdue(self, now_ms: int) -> List[Any]:
        """Публичная версия _reap: вызывается таймером, когда ленты нет."""
        return self._reap(now_ms)

    def _close(self, c: SeriesCandidate, ts_override: Optional[int] = None) -> List[Any]:
        rep = c.reported > 0
        ev: List[Any] = []
        if not rep and c.count >= self.d.report_min_repeats:
            reg = c.regularity(self.d)
            jit = c.jitter_ratio(self.d)
            med = c.median_interval_ms()
            if (
                reg >= self.d.report_min_regular
                and jit <= self.d.jitter_ratio_max
                and med >= self.d.min_report_interval_ms
            ):
                rep = True
                c.reported = 1
                self._stats["reported"] += 1
                self._reported_history.append(self._signal(c, reported=True))
        ev.append(self._signal(c, reported=rep))
        self._events.append(ev[-1])
        self._unregister(c)
        self._stats["candidates_closed"] += 1
        return ev

    def _signal(self, c: SeriesCandidate, reported: bool) -> Signal:
        base = c.base_ms or c.last_interval_ms
        period = c.median_interval_ms() or base
        # Жёсткий фильтр: медианный интервал меньше порога отчёта —
        # это не робот. Сигнал остаётся (виден в статистике как
        # отброшенный), но reported=False и в топ-5 не попадёт.
        if period < self.d.min_report_interval_ms or base < self.d.min_report_interval_ms:
            reported = False
        return Signal(
            symbol=self.symbol,
            side=c.side,
            qty_variants=tuple(sorted(c.qty_variants)),
            hits=c.count,
            base_interval_ms=base,
            period_ms=period,
            start_ms=c.start_ms,
            end_ms=c.last_ms,
            regularity=c.regularity(self.d),
            jitter_ratio=c.jitter_ratio(self.d),
            price_first=c.first_price,
            price_last=c.last_price,
            sum_qty=c.qty_sum,
            reported=reported,
            preset=self.cfg.preset_name,
        )

    # ------------------------------------------------------------------
    # Ёмкость
    # ------------------------------------------------------------------
    def _enforce_cap(self, side: int, ev: List[Any]) -> None:
        """
        Ограничить число активных кандидатов стороны.
        Вытесняем наименее подтверждённые (наименьший count), при
        равенстве — самые старые. min-heap по (count, start_ms) строится
        только при переполнении, а не на каждом шаге.
        """
        od = self._active.get(side)
        if od is None:
            return
        cap = self.d.max_active_candidates
        overflow = len(od) - cap
        if overflow <= 0:
            return
        heap: List[Tuple[int, int, int, SeriesCandidate]] = [
            (c.count, c.start_ms, c.heap_seq, c) for c in od
        ]
        heapify(heap)
        for _ in range(overflow):
            if not heap:
                break
            _, _, _, victim = heappop(heap)
            self._stats["evictions"] += 1
            if victim.count >= self.d.report_min_repeats:
                ev.extend(self._close(victim))
            else:
                self._unregister(victim)

    def flush(self) -> List[Any]:
        """
        Закрыть все активные серии (конец дня / конца файла).
        Возвращает все оставшиеся Signal, включая те, что уже были
        в _events — это ОДИН окончательный список, не дублируйте
        ev(из on_trade) и flush() в одном проходе.
        """
        out: List[Any] = []
        for side in list(self._active):
            od = self._active.get(side)
            if od is None:
                continue
            for c in list(od):
                out.extend(self._close(c))
        self._deadline_heap = []
        return out

    # ------------------------------------------------------------------
    # Снимок активных
    # ------------------------------------------------------------------
    def snapshot(self, now_ms: Optional[int] = None) -> List[Snapshot]:
        """
        Live-строки по активным сериям. Удаление/сортировка не нужны —
        кандидаты и так лежат по свежести.
        """
        d = self.d
        out: List[Snapshot] = []
        for side, od in self._active.items():
            for c in od:
                if c.count < d.min_display_repeats:
                    continue
                base = c.base_ms or c.last_interval_ms
                if base <= 0:
                    continue
                if c.jitter_ratio(d) > d.jitter_ratio_max:
                    continue
                lo, hi = c.qty_minmax()
                s2n = ((c.last_ms + base) - now_ms) / 1000.0 if now_ms is not None else 0.0
                out.append(
                    Snapshot(
                        symbol=self.symbol,
                        side=side,
                        preset=self.cfg.preset_name,
                        qty_variants=(lo, hi) if lo != hi else (lo,),
                        interval_ms=base,
                        hits=c.count,
                        start_ms=c.start_ms,
                        end_ms=c.last_ms,
                        regularity=c.regularity(d),
                        jitter_ratio=c.jitter_ratio(d),
                        price_first=c.first_price,
                        price_last=c.last_price,
                        sum_qty=c.qty_sum,
                        seconds_to_next=s2n,
                    )
                )
        return out

    # ------------------------------------------------------------------
    # Результаты
    # ------------------------------------------------------------------
    @property
    def reported(self) -> Sequence[Signal]:
        """Сигналы, прошедшие порог качества (аналог robots_history)."""
        return self._reported_history

    @property
    def confirmations(self) -> Sequence[Confirmation]:
        return self._confirmed_events

    def pop_events(self) -> List[Any]:
        ev = self._events
        self._events = []
        return ev
