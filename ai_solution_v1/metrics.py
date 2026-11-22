"""
ai_solution_v1/metrics.py
=========================
Сравнение детектора с эталоном Aniscan: TP / FP / FN, Precision / Recall.

Пайплайн:
    1) лента CScalp (JSONL) -> DetectorRegistry -> наши Signal;
    2) эталон aniscan_robots_*.jsonl -> ReferenceRobot;
    3) матчинг по правилу:
         тикер совпадает,
         сторона совпадает (BUY/SELL),
         интервал отличается не больше чем на --tol (по умолчанию 20%),
         момент серии (у нас — конец серии end_ms, у эталона —
         createDttm) попадает в ОДНО 5-минутное окно;
    4) TP = наш сигнал, нашедший пару в эталоне;
       FN = эталонный робот, которому не нашлось пары;
       FP = наш сигнал без пары.

Формат эталона Aniscan (проверено на data/aniscan_robots_2026-11-18.jsonl):
    {"ticker": "AKFB", "operationType": "BUY", "period": 14,
     "minLot": 357, "maxLot": 357, "createDttm": "...+04:00",
     "eventType": "INSERT"|"DELETE", "robot": {"id": "..."}, ...}
    period      — интервал между сделками в СЕКУНДАХ
    createDttm  — время события (МСК+1; приводится к MSK по смещению)
    eventType   — INSERT: робот появился; DELETE: робот закончил.
                  Берём INSERT как "время появления робота", DELETE — как
                  запасной вариант, если INSERT нет (см. load_reference).

Только чтение файлов. Ни сети, ни GUI, ни торговли.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, TextIO, Tuple

from ai_solution_v1.detect.interval import Signal
from ai_solution_v1.pipeline import PipelineConfig, RunReport, _interval_label, run_file

__all__ = [
    "ReferenceRobot",
    "MatchConfig",
    "MatchResult",
    "load_reference",
    "run_detector",
    "match",
    "format_comparison",
    "compare",
]

# Окно сопоставления: 5 минут (по требованию).
WINDOW_MS = 5 * 60 * 1000

# Aniscan отдаёт время в часовом поясе +04:00 (проверено на файлах 11-09..11-19).
_ANISCAN_TZ = timezone(timedelta(hours=4))


# ---------------------------------------------------------------------------
# Эталон
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ReferenceRobot:
    """Один робот из эталона Aniscan."""

    ticker: str
    side: int                     # 1 = buy, -1 = sell
    interval_ms: int              # period(сек) * 1000
    lots_min: int
    lots_max: int
    ts_ms: int                    # createDttm в мс (UTC/абсолютное)
    event_type: str               # INSERT / DELETE
    robot_id: str = ""
    instrument: str = ""

    def key(self) -> Tuple[str, int]:
        return (self.ticker, self.side)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "side": "buy" if self.side == 1 else "sell",
            "interval_ms": self.interval_ms,
            "lots": (self.lots_min if self.lots_min == self.lots_max
                     else f"{self.lots_min}-{self.lots_max}"),
            "ts_ms": self.ts_ms,
            "event": self.event_type,
        }


def _side_id(value: Any) -> int:
    s = str(value or "").strip().upper()
    if s in ("BUY", "B"):
        return 1
    if s in ("SELL", "S"):
        return -1
    return 0


def _parse_dttm(value: Any) -> int:
    """
    Разобрать createDttm -> мс (абсолютное время, UTC).

    Поддерживает:
      * ISO со смещением: "2026-11-19T00:36:04.984896+04:00";
      * ISO с 'Z';
      * ISO без смещения — считаем Aniscan-зоной (+04:00);
      * "2026-11-19 00:36:04" (без 'T').
    """
    if not value:
        return 0
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if "T" not in s:
        s = s.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_ANISCAN_TZ)
    return int(dt.timestamp() * 1000)


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def load_reference(
    path: str,
    *,
    symbols: Optional[Iterable[str]] = None,
    only_insert: bool = True,
) -> Tuple[List[ReferenceRobot], Dict[str, int]]:
    """
    Загрузить эталон Aniscan.

    only_insert=True (по умолчанию) — берём только eventType=INSERT:
    это момент ПОЯВЛЕНИЯ робота. DELETE приходит позже (конец серии)
    и смещает окно. Если для (тикер, сторона) записей INSERT нет,
    берём DELETE как запасной вариант (чтобы не терять роботов).

    Возвращает (список, статистика загрузки).
    """
    stats = {"lines": 0, "rows": 0, "insert": 0, "delete": 0, "bad": 0,
             "filtered_symbol": 0, "filtered_not_detected": 0}
    sym_filter = set(symbols) if symbols else None
    inserts: List[ReferenceRobot] = []
    deletes: List[ReferenceRobot] = []

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stats["lines"] += 1
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except (ValueError, TypeError):
                stats["bad"] += 1
                continue
            if not isinstance(d, dict):
                stats["bad"] += 1
                continue
            ticker = str(d.get("ticker") or "").strip().upper()
            if not ticker:
                stats["bad"] += 1
                continue
            if sym_filter is not None and ticker not in sym_filter:
                stats["filtered_symbol"] += 1
                continue
            side = _side_id(d.get("operationType") or d.get("side"))
            if side == 0:
                stats["bad"] += 1
                continue
            period_s = _to_int(d.get("period"))
            if period_s <= 0:
                stats["bad"] += 1
                continue
            ts = _parse_dttm(d.get("createDttm") or d.get("lastChangeDttm"))
            if ts <= 0:
                stats["bad"] += 1
                continue
            lo = _to_int(d.get("minLot"))
            hi = _to_int(d.get("maxLot")) or lo
            ev = str(d.get("eventType") or "INSERT").upper()
            robot = d.get("robot") or {}
            rec = ReferenceRobot(
                ticker=ticker,
                side=side,
                interval_ms=period_s * 1000,
                lots_min=lo,
                lots_max=hi,
                ts_ms=ts,
                event_type=ev,
                robot_id=str(robot.get("id") or "") if isinstance(robot, dict) else "",
                instrument=str(d.get("instrumentType") or ""),
            )
            if ev == "DELETE":
                deletes.append(rec)
                stats["delete"] += 1
            else:
                inserts.append(rec)
                stats["insert"] += 1

    rows: List[ReferenceRobot] = inserts if only_insert else inserts + deletes
    if only_insert:
        # Для пар, у которых есть только DELETE, добавляем DELETE.
        have = {(r.ticker, r.side) for r in inserts}
        for r in deletes:
            if (r.ticker, r.side) not in have:
                rows.append(r)
    rows.sort(key=lambda r: r.ts_ms)
    stats["rows"] = len(rows)
    return rows, stats


# ---------------------------------------------------------------------------
# Наши сигналы
# ---------------------------------------------------------------------------
def run_detector(
    tape_path: str,
    *,
    symbols: Optional[Sequence[str]] = None,
    limit_lines: int = 0,
    overrides: Optional[Mapping[str, Any]] = None,
    progress: bool = True,
    out: Optional[TextIO] = None,
) -> Tuple[List[Signal], RunReport]:
    """
    Прогнать ленту через детектор и вернуть (все сигналы, отчёт прогона).

    Возвращаются ВСЕ закрытые серии (не только reported=True): матчинг
    по смыслу сравнивает "что нашёл детектор" с эталоном, а фильтр
    качества можно применить отдельно (min_hits / min_interval).
    """
    cfg = PipelineConfig(
        file=Path(tape_path),
        symbols=tuple(symbols) if symbols else None,
        global_overrides=dict(overrides or {}),
        limit_lines=limit_lines,
        progress=progress,
    )
    # Собираем сигналы тем же прогоном, что и отчёт: используем
    # внутренний сборщик через DetectorRegistry напрямую, чтобы не
    # прогонять файл дважды.
    signals, report = _run_and_capture(cfg, out)
    return signals, report


def _run_and_capture(cfg: PipelineConfig, out: Optional[TextIO]) -> Tuple[List[Signal], RunReport]:
    """
    Прогон ленты с перехватом сигналов.

    pipeline.run_file собирает статистику, но не возвращает сами
    Signal. Здесь тот же обход повторяется с раскладкой событий
    в список — так metrics не зависит от внутренностей RunReport.
    """
    import json as _json
    import time as _time

    from ai_solution_v1.config import SettingsError
    from ai_solution_v1.detect.interval import Confirmation, parse_trade
    from ai_solution_v1.detect.registry import DetectorRegistry
    from ai_solution_v1.pipeline import (
        RunReport,
        _initial_configs,
        _merged_overrides,
        count_lines,
        format_progress,
        iter_lines,
    )

    path = cfg.file
    if not path.exists():
        raise FileNotFoundError(f"файл ленты не найден: {path}")

    stdout = out if out is not None else sys.stdout
    is_tty = bool(getattr(stdout, "isatty", lambda: False)())
    total_lines = count_lines(path) if cfg.progress else 0
    if cfg.limit_lines and total_lines:
        total_lines = min(total_lines, cfg.limit_lines)

    reg = DetectorRegistry(configs=_initial_configs(cfg), allowed=cfg.symbols)
    report = RunReport(file=str(path))
    signals: List[Signal] = []
    seen: set[str] = set()
    lazy_all = cfg.symbols is None
    glob_ov = dict(cfg.global_overrides or {})
    tick_ov = cfg.ticker_overrides or {}

    loads = _json.loads
    lines_read = bad = blank = trades = ignored = 0
    t0 = _time.perf_counter()
    next_progress = cfg.progress_every

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
            try:
                det = reg.add_ticker(sym, _merged_overrides(sym, glob_ov, tick_ov))
            except SettingsError:
                bad += 1
                continue
        tr = parse_trade(raw)
        if tr is None:
            bad += 1
            continue
        ev = det.on_trade(tr)
        trades += 1
        seen.add(sym)
        if ev:
            for e in ev:
                if isinstance(e, Confirmation):
                    report.confirmations += 1
                elif isinstance(e, Signal):
                    signals.append(e)
        if cfg.progress and is_tty and lines_read >= next_progress:
            now = _time.perf_counter()
            if total_lines:
                stdout.write(format_progress(lines_read, total_lines,
                                             lines_read / max(now - t0, 1e-9)) + "\r")
                stdout.flush()
            next_progress = lines_read + cfg.progress_every

    for e in reg.flush():
        if isinstance(e, Signal):
            signals.append(e)

    elapsed = _time.perf_counter() - t0
    if cfg.progress and is_tty:
        stdout.write(format_progress(lines_read, total_lines or max(lines_read, 1),
                                     lines_read / max(elapsed, 1e-9)) + "\n")
        stdout.flush()

    report.elapsed_sec = elapsed
    report.lines_read = lines_read
    report.lines_bad = bad
    report.lines_blank = blank
    report.trades_fed = trades
    report.trades_ignored = ignored
    report.signals = len(signals)
    report.reported_signals = sum(1 for s in signals if s.reported)
    report.symbols_seen = len(seen)
    report.symbols_tracked = len(reg)
    return signals, report


# ---------------------------------------------------------------------------
# Матчинг
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class MatchConfig:
    """Параметры матчинга."""

    window_ms: int = WINDOW_MS   # 5 минут
    interval_tol: float = 0.20   # ±20%
    min_hits: int = 0            # 0 = все наши сигналы
    require_reported: bool = False
    # Жёсткий фильтр: серии с медианным интервалом меньше этого НЕ
    # сравниваются с эталоном — это шум/пачки сделок, а не сетка.
    # 1000 мс = 1 с (эталон Aniscan: сетки 6-131 с).
    min_interval_ms: int = 1000


@dataclass(slots=True)
class MatchResult:
    """Результат сопоставления."""

    tp: int = 0
    fp: int = 0
    fn: int = 0
    our_signals: int = 0
    ref_robots: int = 0
    tp_pairs: List[Tuple[Signal, ReferenceRobot]] = field(default_factory=list)
    fp_signals: List[Signal] = field(default_factory=list)
    fn_robots: List[ReferenceRobot] = field(default_factory=list)
    ref_by_symbol: Dict[str, int] = field(default_factory=dict)
    our_by_symbol: Dict[str, int] = field(default_factory=dict)

    @property
    def precision(self) -> float:
        d = self.tp + self.fp
        return self.tp / d if d else 0.0

    @property
    def recall(self) -> float:
        d = self.tp + self.fn
        return self.tp / d if d else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tp": self.tp, "fp": self.fp, "fn": self.fn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "our_signals": self.our_signals,
            "ref_robots": self.ref_robots,
        }


def _within(a_ms: int, b_ms: int, window_ms: int) -> bool:
    """Попадают ли два момента в ОДНО окно заданной длины."""
    if a_ms <= 0 or b_ms <= 0:
        return False
    return abs(a_ms - b_ms) <= window_ms


def match(
    our: Sequence[Signal],
    ref: Sequence[ReferenceRobot],
    cfg: Optional[MatchConfig] = None,
) -> MatchResult:
    """
    Сопоставить наши сигналы с эталоном (жадно, один-к-одному).

    Кандидат-пара: тот же тикер, та же сторона, |Δинтервал| <=
    interval_tol * ref.interval (относительно ЭТАЛОНА: мы не имеем
    права раздувать допуск для коротких интервалов), и оба момента в
    одном окне window_ms.

    Жадность: сначала пары с наименьшей |Δинтервал|, затем с
    наименьшим |Δ| времени. Каждый наш сигнал и каждый эталонный
    робот используются не более одного раза.
    """
    cfg = cfg or MatchConfig()
    res = MatchResult()
    res.ref_robots = len(ref)

    def _cfger_iv(s: Signal) -> int:
        """Медианный интервал нашей серии (period_ms, иначе база)."""
        return s.period_ms or s.base_interval_ms

    ours = [s for s in our
            if s.hits >= cfg.min_hits
            and (s.reported or not cfg.require_reported)
            and _cfger_iv(s) >= cfg.min_interval_ms]
    res.our_signals = len(ours)

    for s in ours:
        res.our_by_symbol[s.symbol] = res.our_by_symbol.get(s.symbol, 0) + 1
    for r in ref:
        res.ref_by_symbol[r.ticker] = res.ref_by_symbol.get(r.ticker, 0) + 1

    # Индекс эталона по (тикер, сторона) — без полного перебора.
    by_key: Dict[Tuple[str, int], List[ReferenceRobot]] = {}
    for r in ref:
        by_key.setdefault(r.key(), []).append(r)

    # Собираем ВСЕ допустимые пары, затем жадно выбираем.
    pairs: List[Tuple[float, float, int, int, Signal, ReferenceRobot]] = []
    for si, s in enumerate(ours):
        cands = by_key.get((s.symbol, s.side))
        if not cands:
            continue
        our_iv = s.base_interval_ms or s.period_ms
        for ri, r in enumerate(cands):
            if our_iv <= 0 or r.interval_ms <= 0:
                continue
            rel = abs(our_iv - r.interval_ms) / r.interval_ms
            if rel > cfg.interval_tol:
                continue
            dt = abs(s.end_ms - r.ts_ms)
            if dt > cfg.window_ms:
                continue
            pairs.append((rel, dt, si, ri, s, r))

    pairs.sort(key=lambda p: (p[0], p[1]))
    used_o: set[int] = set()
    used_r: set[Tuple[str, int, int]] = set()
    for rel, dt, si, ri, s, r in pairs:
        if si in used_o:
            continue
        rkey = (r.ticker, r.side, ri)
        if rkey in used_r:
            continue
        used_o.add(si)
        used_r.add(rkey)
        res.tp_pairs.append((s, r))

    res.tp = len(res.tp_pairs)
    res.fp_signals = [s for i, s in enumerate(ours) if i not in used_o]
    res.fp = len(res.fp_signals)
    res.fn_robots = [r for i, r in enumerate(ref) if (r.ticker, r.side, i) not in used_r]
    res.fn = len(res.fn_robots)
    return res


# ---------------------------------------------------------------------------
# Отчёт
# ---------------------------------------------------------------------------
def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def format_comparison(
    res: MatchResult,
    report: RunReport,
    ref_stats: Mapping[str, int],
    *,
    top_n: int = 10,
) -> str:
    """Текстовый отчёт для консоли."""
    rows: List[str] = []
    rows.append("=" * 72)
    rows.append("СРАВНЕНИЕ С ЭТАЛОНОМ ANISCAN")
    rows.append("=" * 72)
    rows.append(f"Лента:            {report.file}")
    rows.append(f"Строк: {_fmt(report.lines_read)}  сделок: {_fmt(report.trades_fed)}  "
                f"время: {report.elapsed_sec:.1f} с")
    rows.append(f"Эталон:           строк {_fmt(ref_stats.get('lines', 0))} -> "
                f"роботов {_fmt(ref_stats.get('rows', 0))} "
                f"(INSERT {_fmt(ref_stats.get('insert', 0))}, "
                f"DELETE {_fmt(ref_stats.get('delete', 0))}, "
                f"мусор {_fmt(ref_stats.get('bad', 0))})")
    rows.append(f"Наших сигналов:   {_fmt(res.our_signals)}")
    rows.append("")
    rows.append(f"TP = {_fmt(res.tp)}   FP = {_fmt(res.fp)}   FN = {_fmt(res.fn)}")
    rows.append(f"Precision = {_pct(res.precision)}   "
                f"Recall = {_pct(res.recall)}   F1 = {_pct(res.f1)}")
    rows.append("")
    rows.append("Найдено по тикерам (эталон / мы):")
    all_syms = sorted(set(res.ref_by_symbol) | set(res.our_by_symbol))
    for sym in all_syms[:25]:
        r_n = res.ref_by_symbol.get(sym, 0)
        o_n = res.our_by_symbol.get(sym, 0)
        mark = "ok" if r_n and o_n else ("мы только шумим" if o_n else "пропуск")
        rows.append(f"  {sym:<10} эталон {r_n:>4} | мы {o_n:>5}   {mark}")
    if len(all_syms) > 25:
        rows.append(f"  ... ещё {len(all_syms) - 25} тикеров")

    rows.append("")
    rows.append(f"Топ-{top_n} роботов по версии ANISCAN (по числу операций period):")
    # Полный список эталона: найденные (tp_pairs) + не найденные (fn).
    # Сортируем по времени появления (как у Aniscan), затем интервал.
    ref_all = [r for _, r in res.tp_pairs] + list(res.fn_robots)
    ref_sorted = sorted(ref_all, key=lambda r: r.ts_ms)
    # "Эталонные роботы" в порядке появления для сравнения с нашими.
    rows.append(f"  {'тикер':<8}{'сторона':<7}{'интервал':>10}{'лоты':>12}   {'время (MSK)':<20}{'наш':<5}")
    for r in ref_sorted[:top_n]:
        ts = datetime.fromtimestamp(r.ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
        found = any(pr is r for _, pr in res.tp_pairs)
        rows.append(f"  {r.ticker:<8}{('buy' if r.side == 1 else 'sell'):<7}"
                    f"{_interval_label(r.interval_ms):>10}"
                    f"{(str(r.lots_min) if r.lots_min == r.lots_max else f'{r.lots_min}-{r.lots_max}'):>12}   "
                    f"{ts:<20}{'да' if found else 'НЕТ':<5}")

    rows.append("")
    rows.append(f"--- ПРИМЕРЫ TP (совпавшие пары), до {top_n} штук ---")
    if res.tp_pairs:
        rows.append(f"  {'#':<3}{'тикер':<8}{'сторона':<7}{'наш интервал':>14}"
                    f"{'Aniscan':>10}{'Δ, %':>8}{'Δ время, с':>12}  наша серия / событие Aniscan")
        for i, (s, r) in enumerate(res.tp_pairs[:top_n], start=1):
            our_iv = s.period_ms or s.base_interval_ms
            delta_pct = 100.0 * (our_iv - r.interval_ms) / r.interval_ms if r.interval_ms else 0.0
            delta_s = abs(s.end_ms - r.ts_ms) / 1000.0
            our_time = datetime.fromtimestamp(s.end_ms / 1000).strftime("%H:%M:%S")
            ref_time = datetime.fromtimestamp(r.ts_ms / 1000).strftime("%H:%M:%S")
            rows.append(
                f"  {i:<3}{s.symbol:<8}{('buy' if s.side == 1 else 'sell'):<7}"
                f"{_interval_label(our_iv):>14}{_interval_label(r.interval_ms):>10}"
                f"{delta_pct:>8.1f}{delta_s:>12.0f}  "
                f"{our_time} (hits={s.hits}) / {ref_time} (Aniscan period={r.interval_ms // 1000}с)"
            )
    else:
        rows.append("  Совпадений нет (TP = 0).")
    rows.append("")

    rows.append(f"Топ-{top_n} роботов по версии НАШЕГО ДЕТЕКТОРА (по числу серий):")
    # Сгруппировать наши сигналы по (тикер, сторона, интервал 100 мс).
    groups: Dict[Tuple[str, int, int], List[Signal]] = {}
    for s in res.fp_signals + [s for s, _ in res.tp_pairs]:
        iv = (s.base_interval_ms or s.period_ms)
        bucket = int(round(iv / 100.0)) * 100 or iv
        groups.setdefault((s.symbol, s.side, bucket), []).append(s)
    ordered = sorted(groups.items(), key=lambda kv: -len(kv[1]))[:top_n]
    rows.append(f"  {'тикер':<8}{'сторона':<7}{'интервал':>10}{'серий':>7}{'сделок':>8}{'TP?':>5}")
    tp_sig_ids = {id(s) for s, _ in res.tp_pairs}
    for (sym, side, bucket), sigs in ordered:
        n_tp = sum(1 for s in sigs if id(s) in tp_sig_ids)
        rows.append(f"  {sym:<8}{('buy' if side == 1 else 'sell'):<7}"
                    f"{_interval_label(bucket):>10}{len(sigs):>7}"
                    f"{sum(s.hits for s in sigs):>8}{n_tp:>5}")
    if not ordered:
        rows.append("  (наш детектор не нашёл ни одной серии)")

    rows.append("")
    rows.append("Правило матчинга: тот же тикер и сторона, интервал эталона ±20%, "
                "наша серия и событие Aniscan — в одном окне 5 минут.")
    return "\n".join(rows)


def compare(
    tape_path: str,
    ref_path: str,
    *,
    symbols: Optional[Sequence[str]] = None,
    limit_lines: int = 0,
    overrides: Optional[Mapping[str, Any]] = None,
    match_cfg: Optional[MatchConfig] = None,
    progress: bool = True,
    out: Optional[TextIO] = None,
    top_n: int = 10,
) -> Tuple[MatchResult, RunReport, Dict[str, int]]:
    """Полный цикл: лента -> детектор -> эталон -> матчинг -> результат."""
    signals, report = run_detector(
        tape_path, symbols=symbols, limit_lines=limit_lines,
        overrides=overrides, progress=progress, out=out,
    )
    # Эталон фильтруем по тем же тикерам, что реально были в ленте:
    # роботы по фьючерсам/валюте в ленте CScalp отсутствуют, считать
    # их FN было бы нечестно к детектору (их там физически нет).
    ref_syms = symbols if symbols else None
    ref, ref_stats = load_reference(ref_path, symbols=ref_syms)
    res = match(signals, ref, match_cfg)
    return res, report, ref_stats


# ---------------------------------------------------------------------------
# CLI-вход (используется из ai_solution_v1.cli через --compare-aniscan)
# ---------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Сравнение детектора с эталоном Aniscan")
    p.add_argument("--file", required=True, help="лента JSONL (CScalp)")
    p.add_argument("--compare-aniscan", required=True, dest="ref",
                   help="эталон aniscan_robots_*.jsonl")
    p.add_argument("--symbol", nargs="+", default=None)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--interval-tol", type=float, default=0.20)
    p.add_argument("--window-sec", type=int, default=300)
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--no-progress", action="store_true")
    args = p.parse_args(argv)

    res, report, ref_stats = compare(
        args.file,
        args.ref,
        symbols=args.symbol,
        limit_lines=args.limit,
        progress=not args.no_progress,
        match_cfg=MatchConfig(window_ms=args.window_sec * 1000,
                              interval_tol=args.interval_tol),
        top_n=args.top,
    )
    print(format_comparison(res, report, ref_stats, top_n=args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
