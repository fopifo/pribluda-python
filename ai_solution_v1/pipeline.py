"""
ai_solution_v1/pipeline.py
==========================
Оркестратор офлайн-прогона: JSONL-лента CScalp -> детекторы -> отчёт.

Только чтение файла, разбор строки и вызов детекторов. Ни сети, ни
GUI, ни торговли — модуль не знает, что такое ордер.

Принципы:
  * Файл читается ПОТОКОВО (одна строка в памяти). Ленты за день —
    30-400 МБ; ни pandas, ни full-load.
  * Прогресс-бар печатается самим pipeline (любой прогон >5 с обязан
    показывать прогресс). Процент считается по быстрому подсчёту
    переводов строк в сырых байтах — O(1) по памяти.
  * Битые/обрезанные строки (мост ещё дописывает) не роняют прогон, а
    считаются в lines_bad.
  * Чужие тикеры отсекаются до parse_trade — самый дорогой шаг
    (json.loads) делается только для нужных тикеров, когда задан
    --symbol.
  * Нет --symbol — состав тикеров заранее неизвестен, поэтому детектор
    создаётся ЛЕНИВО на первом появлении тикера (это же поведение
    дают 86 тикеров из ticker_settings.json).
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, TextIO, Tuple

from ai_solution_v1.config import SettingsError
from ai_solution_v1.detect.interval import Confirmation, Signal, parse_trade
from ai_solution_v1.detect.registry import DetectorRegistry, RegistryStats

__all__ = [
    "PipelineConfig",
    "RunReport",
    "RobotAggregate",
    "run_file",
    "iter_lines",
    "count_lines",
    "format_progress",
    "format_summary",
]

# Квантование интервала для группировки повторяющихся серий: 100 мс.
# Раньше здесь был бакет в 5 секунд — он склеивал 2.5-7.5 с в одну
# "карточку 5.0 с" и маскировал реальные периоды (NVTK-buy ~500 мс
# уходил в бакет 0 и в топ не попадал вовсе).
# 100 мс достаточно, чтобы джиттер ленты не дробил группу, и при этом
# в отчёте видно реальный период: 500 мс, 1000 мс, 4000 мс.
_INTERVAL_BUCKET_MS = 100


# ---------------------------------------------------------------------------
# Настройки прогона
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class PipelineConfig:
    """Параметры офлайн-прогона (не путать с конфигом детектора)."""

    file: Path
    symbols: Optional[Tuple[str, ...]] = None       # None = все тикеры файла
    global_overrides: Optional[Mapping[str, Any]] = None   # для всех тикеров
    ticker_overrides: Optional[Mapping[str, Mapping[str, Any]]] = None
    limit_lines: int = 0                            # 0 = весь файл
    progress_every: int = 100_000                  # строк между перерисовками
    progress_min_interval: float = 1.0             # сек; бар шевелится не реже
    progress: bool = True
    min_signal_hits: int = 0                        # фильтр топ-списка
    # Жёсткий фильтр топ-5: серии с медианным интервалом меньше порога
    # НЕ показываются в отчёте (0 = выключено). Дублирует фильтр
    # детектора, чтобы мусор не просочился, даже если детектор
    # откатили к старым настройкам.
    min_report_interval_ms: int = 1000
    top_n: int = 5                                  # строк в топ-отчёте
    encoding: str = "utf-8"


# ---------------------------------------------------------------------------
# Агрегат повторяющихся сигналов
# ---------------------------------------------------------------------------
# Ключ группировки: (symbol, side, qty_min, qty_max, interval_bucket_ms)
AggKey = Tuple[str, int, int, int, int]


@dataclass(slots=True)
class RobotAggregate:
    """
    Сводка по одинаковым сериям одного тикера/стороны/объёма/сетки.

    Поля name/hits выбраны так, чтобы CLI мог печатать строку без
    склейки строк вручную.
    """

    key: AggKey
    hits: int = 0                 # сколько серий попало в группу
    total_trades: int = 0         # сумма сделок этих серий
    max_series_hits: int = 0      # самая длинная серия
    reported: int = 0             # сколько прошло порог качества
    first_ms: int = 0
    last_ms: int = 0

    @property
    def symbol(self) -> str:
        return self.key[0]

    @property
    def side(self) -> str:
        return "buy" if self.key[1] == 1 else "sell"

    @property
    def qty(self) -> str:
        lo, hi = self.key[2], self.key[3]
        return str(lo) if lo == hi else f"{lo}-{hi}"

    @property
    def interval_ms(self) -> int:
        return self.key[4]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "interval_sec": round(self.interval_ms / 1000.0, 1),
            "signals": self.hits,
            "total_trades": self.total_trades,
            "max_series_hits": self.max_series_hits,
            "reported": self.reported,
            "first_ms": self.first_ms,
            "last_ms": self.last_ms,
        }


def aggregate_key(sig: Signal) -> AggKey:
    """Ключ группировки серии. O(1), без сортировок."""
    qv = sig.qty_variants
    lo, hi = (qv[0], qv[-1]) if qv else (0, 0)
    base = sig.base_interval_ms or sig.period_ms
    if base <= 0:
        # Нулевая сетка: группа "мусор", будет отсечена фильтром
        # min_report_interval_ms при формировании топа.
        bucket = 0
    else:
        # Округление до 100 мс: 500 мс останется 500 мс, 5000 мс —
        # 5000 мс, а не превратится в "5.0 с между 2.5 и 7.5".
        bucket = int(round(base / _INTERVAL_BUCKET_MS)) * _INTERVAL_BUCKET_MS
        if bucket < _INTERVAL_BUCKET_MS:
            bucket = _INTERVAL_BUCKET_MS
    return (sig.symbol, sig.side, lo, hi, bucket)


def _interval_label(ms: int) -> str:
    """Человекочитаемый интервал: '500 мс', '1.5 с', '5.0 с'."""
    if ms < 1000:
        return f"{ms} мс"
    return f"{ms / 1000.0:.1f} с"


# ---------------------------------------------------------------------------
# Отчёт прогона
# ---------------------------------------------------------------------------
@dataclass
class RunReport:
    """Результат прогона одного файла."""

    file: str = ""
    elapsed_sec: float = 0.0
    lines_read: int = 0
    lines_bad: int = 0
    lines_blank: int = 0
    trades_fed: int = 0
    trades_ignored: int = 0
    confirmations: int = 0
    signals: int = 0
    reported_signals: int = 0
    symbols_seen: int = 0
    symbols_tracked: int = 0
    top: List[RobotAggregate] = field(default_factory=list)
    groups_total: int = 0
    warnings: List[str] = field(default_factory=list)
    counters: RegistryStats = field(default_factory=RegistryStats)

    @property
    def lines_per_sec(self) -> float:
        return self.lines_read / self.elapsed_sec if self.elapsed_sec > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file,
            "elapsed_sec": round(self.elapsed_sec, 2),
            "lines_read": self.lines_read,
            "lines_bad": self.lines_bad,
            "lines_blank": self.lines_blank,
            "trades_fed": self.trades_fed,
            "trades_ignored": self.trades_ignored,
            "confirmations": self.confirmations,
            "signals": self.signals,
            "reported_signals": self.reported_signals,
            "symbols_seen": self.symbols_seen,
            "symbols_tracked": self.symbols_tracked,
            "groups_total": self.groups_total,
            "lines_per_sec": round(self.lines_per_sec),
            "top": [a.to_dict() for a in self.top],
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# Чтение
# ---------------------------------------------------------------------------
def iter_lines(path: Path, encoding: str = "utf-8") -> Iterable[str]:
    """
    Построчный итератор по JSONL: одна строка в памяти.

    errors="replace": один битый байт в середине 400 МБ ленты не должен
    ронять весь прогон — такая строка отсеется как lines_bad.
    """
    with open(path, "r", encoding=encoding, errors="replace", newline=None) as fh:
        for line in fh:
            yield line


def count_lines(path: Path, chunk: int = 8 * 1024 * 1024) -> int:
    """
    Быстрый подсчёт переводов строк в сырых байтах — только для процента
    прогресс-бара. Чтение блоками, память O(chunk).
    """
    total = 0
    try:
        with open(path, "rb") as fh:
            while True:
                block = fh.read(chunk)
                if not block:
                    break
                total += block.count(b"\n")
    except OSError:
        return 0
    return total


# ---------------------------------------------------------------------------
# Прогресс
# ---------------------------------------------------------------------------
class ProgressBar:
    """
    Прогресс-бар, который реально виден на экране.

    Главная беда прежней версии — не «нет flush», а ГРАНУЛЯРНОСТЬ:
    бар перерисовывался раз в N строк, а не по времени. На медленном
    прогоне (2K строк/с) порог в 100 000 строк = 50 секунд «замёрзшей»
    шкалы, а flush был — просто ему нечего было сбрасывать.

    Теперь два независимых триггера:
      1) по ВРЕМЕНИ — не реже, чем раз в `min_interval` секунд
         (по умолчанию 1.0 с), независимо от скорости;
      2) по СТРОКАМ — раз в `every_lines` строк (для очень быстрых
         прогонов, чтобы не дёргать консоль лишний раз).

    Вывод:
      * на TTY: одна строка с \\r, перерисовка на месте;
      * НЕ на TTY (редирект в файл): раз в 30 с с новой строкой, чтобы
        в логе была история, а не только финал.
    Сброс буфера тройной: stream.flush() -> stream.buffer.flush() ->
    os.fsync не нужен (это не файл), но при наличии fileno пишем
    повторно, если поток остался блокирующим.
    """

    __slots__ = ("_stream", "_total", "_is_tty", "_min_dt", "_every",
                 "_last_t", "_last_lines", "_last_emit", "_enabled",
                 "_start_t", "drawn")

    def __init__(
        self,
        stream: TextIO,
        total: int,
        enabled: bool = True,
        min_interval: float = 1.0,
        every_lines: int = 100_000,
    ) -> None:
        self._stream = stream
        self._total = total
        self._is_tty = bool(getattr(stream, "isatty", lambda: False)())
        self._min_dt = max(0.1, float(min_interval))
        self._every = max(1_000, int(every_lines))
        self._enabled = bool(enabled)
        self._last_t = time.perf_counter()
        self._last_lines = 0
        self._last_emit = 0.0
        self._start_t = self._last_t
        self.drawn = 0

    # ------------------------------------------------------------------
    def maybe(self, lines: int) -> bool:
        """
        Перерисовать бар, если настало время/набрались строки.
        Вызывается на КАЖДОЙ строке, но дорогой код выполняется редко
        (проверка — сравнение двух float).
        """
        if not self._enabled:
            return False
        now = time.perf_counter()
        dt = now - self._last_t
        if dt < self._min_dt and (lines - self._last_lines) < self._every:
            return False
        self._last_t = now
        self._last_lines = lines
        self._render(lines, now)
        return True

    def finish(self, lines: int) -> None:
        """Финальная перерисовка 100% + перевод строки."""
        if not self._enabled:
            return
        self._render(lines, time.perf_counter(), final=True)

    # ------------------------------------------------------------------
    def _render(self, lines: int, now: float, final: bool = False) -> None:
        elapsed = max(now - self._start_t, 1e-9)
        rate = lines / elapsed
        total = self._total or (lines if final else 0)
        text = format_progress(lines, total, rate)
        if self._is_tty:
            text = text + "\r"
            if final:
                text = text[:-1] + "\n"
        else:
            # Не-TTY: только редкие снимки, каждый с новой строки.
            if not final and now - self._last_emit < 30.0 and self.drawn:
                return
            text = text + "\n"
        self._last_emit = now
        self.drawn += 1
        _raw_emit(self._stream, text)


def _raw_emit(stream: TextIO, text: str) -> None:
    """
    Вывести строку и сбросить ВСЕ буферы, какие бывают у stdout:
      * stream.write + stream.flush()       — буфер TextIOWrapper;
      * stream.buffer.write + .flush()      — буфер BufferedWriter
        (на случай, если текстовый слой только накапливает).
    Ошибки закрытого/неподключаемого потока не роняют прогон.
    """
    try:
        stream.write(text)
        stream.flush()
    except (ValueError, OSError, AttributeError):
        return
    try:
        buf = getattr(stream, "buffer", None)
        if buf is not None:
            buf.flush()
    except (ValueError, OSError, AttributeError):
        pass


def _human(n: float) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return f"{int(n)}"


def _human_time(sec: float) -> str:
    if sec < 60:
        return f"{sec:.0f}с"
    if sec < 3600:
        return f"{sec / 60:.1f}мин"
    return f"{sec / 3600:.1f}ч"


def format_progress(done: int, total: int, rate: float) -> str:
    """
    Строка прогресс-бара в формате AGENTS.md:
        [pipeline] [######----] 60% | 2.5M/4.2M строк | 125K/с | ETA 14с
    """
    width = 10
    if total > 0:
        pct = min(100.0, 100.0 * done / total)
        filled = int(round(width * pct / 100.0))
        head = f"[{'#' * filled}{'.' * (width - filled)}] {pct:5.1f}%"
        size = f"{_human(done)}/{_human(total)} строк"
    else:
        head = f"[{'.' * width}]   ?.?%"
        size = f"{_human(done)} строк"
    rate_s = f"{_human(rate)}/с" if rate > 0 else ".../с"
    if total > 0 and rate > 0:
        eta_s = f"ETA {_human_time(max(0, total - done) / rate)}"
    else:
        eta_s = "ETA ?"
    return f"[pipeline] {head} | {size} | {rate_s} | {eta_s}"


# ---------------------------------------------------------------------------
# Основной прогон
# ---------------------------------------------------------------------------
def run_file(cfg: PipelineConfig, out: Optional[TextIO] = None) -> RunReport:
    """
    Прогнать исторический JSONL через детекторы и собрать отчёт.

    cfg.symbols = ("SBER",)      -> только этот тикер (остальные не парсятся)
    cfg.symbols = None           -> все тикеры; детектор создаётся лениво
                                    на первом появлении тикера
    cfg.ticker_overrides         -> per-ticker пороги (например из
                                    ticker_settings.json)

    Исключения только для отсутствующего файла / кривого конфига.
    На битых данных — счётчики в отчёте.
    """
    path = Path(cfg.file)
    if not path.exists():
        raise FileNotFoundError(f"файл ленты не найден: {path}")
    if path.is_dir():
        raise IsADirectoryError(f"это не файл ленты: {path}")

    stdout = out if out is not None else sys.stdout
    # Прогресс рисуем, если поток идёт в терминал. Раньше условие
    # включало ещё и `total_lines != 0`, из-за чего при неудачном
    # подсчёте строк бар не рисовался ВООБЩЕ (молчание до Ctrl+C).
    is_tty = bool(getattr(stdout, "isatty", lambda: False)())
    draw_progress = bool(is_tty and cfg.progress)

    total_lines = count_lines(path) if cfg.progress else 0
    if cfg.limit_lines:
        total_lines = min(total_lines, cfg.limit_lines) if total_lines else 0

    # Порог отчёта по интервалу: берём из той же схемы, что и пороги
    # детектора (флаг --min-report-interval-sec или дефолт config.py).
    if not cfg.min_report_interval_ms:
        try:
            from ai_solution_v1.config import DEFAULT_CONFIG as _DCFG

            cfg.min_report_interval_ms = int(round(_DCFG.min_report_interval_sec * 1000))
        except Exception:
            cfg.min_report_interval_ms = 500

    reg = DetectorRegistry(
        configs=_initial_configs(cfg),
        allowed=cfg.symbols,
    )

    report = RunReport(file=str(path))
    agg: Dict[AggKey, RobotAggregate] = {}
    seen: set[str] = set()

    loads = json.loads
    lazy_all = cfg.symbols is None
    glob_ov = dict(cfg.global_overrides or {})
    tick_ov = cfg.ticker_overrides or {}

    lines_read = 0
    bad = 0
    blank = 0
    trades = 0
    ignored = 0
    confirms = 0
    signals = 0
    t0 = time.perf_counter()
    bar = ProgressBar(
        stdout,
        total_lines,
        enabled=cfg.progress,
        min_interval=cfg.progress_min_interval,
        every_lines=cfg.progress_every,
    )

    try:
        for line in iter_lines(path, cfg.encoding):
            lines_read += 1
            if cfg.limit_lines and lines_read > cfg.limit_lines:
                lines_read -= 1
                break
            if not line or not line.strip():
                blank += 1
                continue
            try:
                raw = loads(line)
            except (ValueError, TypeError):
                bad += 1
                continue
            if not isinstance(raw, dict):
                bad += 1
                continue

            sym = raw.get("symbol")
            if not sym:
                bad += 1
                continue

            det = reg.get(sym)
            if det is None:
                if not lazy_all:
                    ignored += 1
                    continue
                # Ленивое создание детектора для нового тикера.
                try:
                    det = reg.add_ticker(sym, _merged_overrides(sym, glob_ov, tick_ov))
                except SettingsError as exc:
                    bad += 1
                    report.warnings.append(f"{sym}: {exc}")
                    continue

            trade = parse_trade(raw)
            if trade is None:
                bad += 1
                continue

            ev = det.on_trade(trade)
            trades += 1
            seen.add(sym)
            if ev:
                for e in ev:
                    if isinstance(e, Confirmation):
                        confirms += 1
                    elif isinstance(e, Signal):
                        signals += 1
                        _accumulate(agg, e)

            # Прогресс — по ВРЕМЕНИ (не реже раза в секунду), а не по
            # счётчику строк: на медленной ленте счётчик молчал минутами.
            bar.maybe(lines_read)

        # Конец файла: закрыть все активные серии.
        for e in reg.flush():
            if isinstance(e, Signal):
                signals += 1
                _accumulate(agg, e)
    finally:
        elapsed = time.perf_counter() - t0
        bar.finish(lines_read)

    report.elapsed_sec = elapsed
    report.lines_read = lines_read
    report.lines_bad = bad
    report.lines_blank = blank
    report.trades_fed = trades
    report.trades_ignored = ignored
    report.confirmations = confirms
    report.signals = signals
    report.reported_signals = sum(a.reported for a in agg.values())
    report.symbols_seen = len(seen)
    report.symbols_tracked = len(reg)
    report.groups_total = len(agg)
    report.counters = reg.stats

    items = [
        a for a in agg.values()
        if a.hits >= cfg.min_signal_hits
        and (cfg.min_report_interval_ms <= 0 or a.interval_ms >= cfg.min_report_interval_ms)
    ]
    items.sort(key=lambda a: (-a.hits, -a.max_series_hits, a.symbol, a.key[1]))
    report.top = items[: cfg.top_n]

    if trades == 0:
        report.warnings.append(
            "сделок не подано: проверь --symbol (такого тикера нет в файле) "
            "и формат строки {\"symbol\",\"side\",\"qty\",\"price\",\"timestamp\"}"
        )
    elif signals == 0:
        report.warnings.append(
            "сделки есть, но серий не найдено: попробуй снизить report_min_repeats "
            "или сузить --symbol до активного тикера"
        )
    return report


def _merged_overrides(
    sym: str,
    global_ov: Mapping[str, Any],
    ticker_ov: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """overrides тикера = global < ticker."""
    merged = dict(global_ov)
    extra = ticker_ov.get(sym)
    if extra:
        merged.update(extra)
    return merged


def _initial_configs(cfg: PipelineConfig) -> Dict[str, Mapping[str, Any]]:
    """
    Стартовый состав реестра.

    * --symbol задан -> создаём эти тикеры сразу (они точно нужны).
    * --symbol нет  -> пусто; тикеры появятся лениво по ленте.
    """
    if not cfg.symbols:
        return {}
    glob_ov = cfg.global_overrides or {}
    tick_ov = cfg.ticker_overrides or {}
    return {sym: _merged_overrides(sym, glob_ov, tick_ov) for sym in cfg.symbols}


def _accumulate(agg: Dict[AggKey, RobotAggregate], sig: Signal) -> None:
    """Свернуть серию в агрегат. O(1)."""
    key = aggregate_key(sig)
    a = agg.get(key)
    if a is None:
        a = RobotAggregate(key=key, first_ms=sig.start_ms, last_ms=sig.end_ms)
        agg[key] = a
    a.hits += 1
    a.total_trades += sig.hits
    if sig.hits > a.max_series_hits:
        a.max_series_hits = sig.hits
    if sig.reported:
        a.reported += 1
    if sig.start_ms and (not a.first_ms or sig.start_ms < a.first_ms):
        a.first_ms = sig.start_ms
    if sig.end_ms and sig.end_ms > a.last_ms:
        a.last_ms = sig.end_ms


# ---------------------------------------------------------------------------
# Текстовый отчёт
# ---------------------------------------------------------------------------
def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def format_summary(report: RunReport) -> str:
    """Короткий отчёт для консоли (10-15 строк + топ-5)."""
    rows: List[str] = [
        f"Файл:              {Path(report.file).name}",
        f"Время:             {report.elapsed_sec:.1f} с "
        f"({_human(report.lines_per_sec)} строк/с)",
        f"Строк прочитано:   {_fmt(report.lines_read)}"
        + (f" (битых {_fmt(report.lines_bad)}, пустых {_fmt(report.lines_blank)})"
           if report.lines_bad or report.lines_blank else ""),
        f"Сделок обработано: {_fmt(report.trades_fed)}"
        + (f" (чужих тикеров {_fmt(report.trades_ignored)})" if report.trades_ignored else ""),
        f"Тикеров:           {report.symbols_tracked} отслежено, {report.symbols_seen} встречено",
        f"Подтверждений:     {_fmt(report.confirmations)}",
        f"Сигналов (серий):  {_fmt(report.signals)}  в {_fmt(report.groups_total)} группах",
        f"Прошли порог:      {_fmt(report.reported_signals)}",
    ]
    if report.top:
        rows.append("")
        rows.append("Топ-5 повторяющихся серий (тикер / сторона / объём / сетка):")
        rows.append(f"  {'тикер':<7}{'сторона':<7}{'qty':<12}{'интервал':>10}{'серий':>7}{'сделок':>8}{'max':>6}")
        for a in report.top:
            rows.append(
                f"  {a.symbol:<7}{a.side:<7}{a.qty:<12}"
                f"{_interval_label(a.interval_ms):>10}{a.hits:>7}{a.total_trades:>8}{a.max_series_hits:>6}"
            )
    if report.warnings:
        rows.append("")
        for w in report.warnings:
            rows.append(f"! {w}")
    return "\n".join(rows)
