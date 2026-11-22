"""
ai_solution_v1/tools_diag.py
===========================
Диагностика интервалов ленты и детектора. ТОЛЬКО ЧТЕНИЕ, без торговли.

Запуск:
    python -m ai_solution_v1.tools_diag --file data/pbsdk_trades_2026-11-19.jsonl \
        --symbol VTBR NVTK --head 20 --limit 300000

Что печатает:
  1) первые N строк: timestamp / symbol / qty / side (шаг ленты);
  2) РАСПРЕДЕЛЕНИЕ ЗАЗОРОВ между сделками тикера (по стороне и без):
     сколько раз встречается зазор 0 мс, 1-5 мс, 1 с, 2 с, 5 с и т.д.;
  3) сколько серий детектор нашёл по каждому ИНТЕРВАЛУ (bucket) —
     при разных порогах --min-report-interval-sec;
  4) гистограмму базовых интервалов найденных серий.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

from ai_solution_v1.pipeline import PipelineConfig, run_file, iter_lines

__all__ = ["main"]

GAP_BUCKETS: List[Tuple[int, str]] = [
    (0, "=0 мс"),
    (1, "1 мс"),
    (5, "2-5 мс"),
    (50, "6-50 мс"),
    (100, "51-100 мс"),
    (500, "101-500 мс"),
    (1000, "0.5-1 с"),
    (2000, "1-2 с"),
    (3000, "2-3 с"),
    (5000, "3-5 с"),
    (10000, "5-10 с"),
    (30000, "10-30 с"),
    (60000, "30-60 с"),
    (10**9, ">60 с"),
]


def _bucket(gap_ms: int) -> str:
    if gap_ms == 0:
        return GAP_BUCKETS[0][1]
    for limit, name in GAP_BUCKETS[1:]:
        if gap_ms <= limit:
            return name
    return GAP_BUCKETS[-1][1]


def _head(path: Path, n: int, symbols: List[str]) -> None:
    print(f"\n=== 1. Первые {n} строк: timestamp / symbol / side / qty ===")
    shown = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            shown += 1
            if shown > n:
                break
            try:
                d = json.loads(line)
            except ValueError:
                print(f"{shown:>3}. <битая строка>")
                continue
            ts = d.get("timestamp")
            print(f"{shown:>3}. ts={ts}  {d.get('symbol'):<8} {d.get('side'):<5} qty={d.get('qty')}")


def _gaps(path: Path, symbols: List[str], limit: int) -> Dict[str, Dict[str, "Counter[str]"]]:
    """
    Распределение зазоров между соседними сделками тикера.
    Ключ: symbol -> 'all' / 'buy' / 'sell' -> bucket -> count.
    """
    last: Dict[Tuple[str, str], int] = {}
    out: Dict[str, Dict[str, Counter]] = {}
    n = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            n += 1
            if limit and n > limit:
                break
            try:
                d = json.loads(line)
            except ValueError:
                continue
            sym = d.get("symbol")
            if sym not in symbols:
                continue
            side = str(d.get("side") or "")
            ts = int(d.get("timestamp") or 0)
            if ts <= 0:
                continue
            per = out.setdefault(sym, {"all": Counter(), "buy": Counter(), "sell": Counter()})
            prev_all = last.get((sym, "*"))
            if prev_all is not None:
                per["all"][_bucket(ts - prev_all)] += 1
            last[(sym, "*")] = ts
            prev_side = last.get((sym, side))
            if prev_side is not None:
                key = "buy" if side == "buy" else "sell"
                per[key][_bucket(ts - prev_side)] += 1
            last[(sym, side)] = ts
    return out


def _print_gaps(gaps: Dict[str, Dict[str, Counter]]) -> None:
    print("\n=== 2. Распределение зазоров между сделками (все подряд / по стороне) ===")
    order = [name for _, name in GAP_BUCKETS]
    for sym, per in gaps.items():
        print(f"\n  {sym}:")
        head = f"  {'зазор':<12}{'все':>10}{'buy':>10}{'sell':>10}"
        print(head)
        for name in order:
            a, b, s = per["all"][name], per["buy"][name], per["sell"][name]
            if a or b or s:
                print(f"  {name:<12}{a:>10}{b:>10}{s:>10}")


def _detector_buckets(path: Path, symbols: List[str], limit: int, threshold_sec: float) -> Counter:
    """
    Прогон детектора с поддержкой разных порогов отчёта.
    Возвращает Counter: базовый интервал найденных серий (мс) -> сколько раз.
    """
    cfg = PipelineConfig(
        file=path,
        symbols=tuple(symbols),
        limit_lines=limit,
        progress=False,
        global_overrides={"min_report_interval_sec": threshold_sec},
        min_report_interval_ms=int(threshold_sec * 1000) or 1,
    )
    report = run_file(cfg, out=sys.stdout)
    c = Counter()
    for a in report.top:
        c[a.interval_ms] += a.hits
    return c


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Диагностика интервалов ленты и детектора")
    p.add_argument("--file", required=True)
    p.add_argument("--symbol", nargs="+", default=["VTBR", "NVTK"])
    p.add_argument("--head", type=int, default=20)
    p.add_argument("--limit", type=int, default=300_000)
    p.add_argument("--thresholds", nargs="+", type=float, default=[0.5, 0.1, 0.0])
    p.add_argument(
        "--full", action="store_true",
        help="прогнать ВЕСЬ файл (весь файл может быть 400 МБ — долго); "
             "по умолчанию ограничиваемся --limit строк",
    )
    args = p.parse_args(argv)

    path = Path(args.file)
    if not path.exists():
        print(f"файл не найден: {path}", file=sys.stderr)
        return 2

    _head(path, args.head, args.symbol)

    # Замер зазоров: по умолчанию по тем же строкам, что и прогон детектора.
    limit = 0 if args.full else args.limit
    _print_gaps(_gaps(path, set(args.symbol), limit))

    print("\n=== 3. Интервалы найденных серий при разных порогах отчёта ===")
    for th in args.thresholds:
        c = _detector_buckets(path, args.symbol, limit, th)
        items = sorted(c.items())
        txt = ", ".join(f"{ms} мс x{n}" for ms, n in items) if items else "нет серий"
        print(f"  min_report_interval={th:.1f} с -> {txt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
