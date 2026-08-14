"""
Приблуда на python — универсальный анализатор отчётов сигналов.

Умеет читать:
  - signals_all_days_*.txt (исторический отчёт по всем дням)
  - signals_report_*.txt (отчёт после фильтрации)
  - live_signals_*.txt (живой лог)

Поддерживает аргументы командной строки:
  --input <файл>       входной файл (если не указан, берётся последний signals_all_days_*.txt)
  --min-repeats N      минимальное число повторов (по умолчанию 4)
  --max-jitter N       максимальный джиттер, мс (по умолчанию 150)
  --max-cv N           максимальный CV% (по умолчанию 0.5)
  --preset NAME        фильтр по пресету (fast_strict или twap_strict)
  --aggregate          вывести агрегированный отчёт (по паттернам)
  --top N              вывести только топ N строк (для агрегированного)

Примеры:
  python tools/analyze.py --input output/signals_all_days_2026-08-14_180114.txt
  python tools/analyze.py --input output/signals_all_days_*.txt --min-repeats 5 --max-cv 0.3 --aggregate --top 30
"""

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"

# Паттерн для строк сигналов (с опциональным временем и ведущими пробелами)
SIGNAL_PATTERN_WITH_TIME = re.compile(
    r'^\s*\[(?P<log_time>\d{2}:\d{2}:\d{2})\]\s+(?P<label>НОВЫЙ|ЗАКРЫТ)\s+'
    r'\[робот-интервал\[(?P<preset>[^\]]+)\]\]\s+'
    r'(?P<symbol>[A-Z0-9]+)\s+(?P<side>buy|sell)\s+'
    r'qty=(?P<qty>[^ ]+)\s+повторов=(?P<repeats>\d+)\s+'
    r'интервал~(?P<interval>[\d.]+)с\s+'
    r'(?:джиттер=(?P<jitter>[\d.]+)мс\s+)?'
    r'(?:стаб=(?P<stability>[\d.]+%)\s+)?'
    r'с (?P<start_h>\d{2}):(?P<start_m>\d{2}):(?P<start_s>\d{2}) '
    r'по (?P<end_h>\d{2}):(?P<end_m>\d{2}):(?P<end_s>\d{2}) '
    r'\(длилось [\d.]+ сек\)$'
)

SIGNAL_PATTERN_NO_TIME = re.compile(
    r'^\s*\[робот-интервал\[(?P<preset>[^\]]+)\]\]\s+'
    r'(?P<symbol>[A-Z0-9]+)\s+(?P<side>buy|sell)\s+'
    r'qty=(?P<qty>[^ ]+)\s+повторов=(?P<repeats>\d+)\s+'
    r'интервал~(?P<interval>[\d.]+)с\s+'
    r'(?:джиттер=(?P<jitter>[\d.]+)мс\s+)?'
    r'(?:стаб=(?P<stability>[\d.]+%)\s+)?'
    r'с (?P<start_h>\d{2}):(?P<start_m>\d{2}):(?P<start_s>\d{2}) '
    r'по (?P<end_h>\d{2}):(?P<end_m>\d{2}):(?P<end_s>\d{2}) '
    r'\(длилось [\d.]+ сек\)$'
)


@dataclass
class SignalRow:
    preset: str
    symbol: str
    side: str
    repeats: int
    interval: float
    jitter_ms: float | None
    qty_str: str
    start_sec: float
    end_sec: float

    @property
    def cv_pct(self) -> float | None:
        if self.jitter_ms is None or self.interval <= 0:
            return None
        return (self.jitter_ms / 1000.0) / self.interval * 100.0


def parse_time(h: str, m: str, s: str) -> float:
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_signal_line(line: str) -> SignalRow | None:
    m = SIGNAL_PATTERN_WITH_TIME.match(line) or SIGNAL_PATTERN_NO_TIME.match(line)
    if not m:
        return None
    jitter = float(m.group("jitter")) if m.group("jitter") else None
    return SignalRow(
        preset=m.group("preset"),
        symbol=m.group("symbol"),
        side=m.group("side"),
        repeats=int(m.group("repeats")),
        interval=float(m.group("interval")),
        jitter_ms=jitter,
        qty_str=m.group("qty"),
        start_sec=parse_time(m.group("start_h"), m.group("start_m"), m.group("start_s")),
        end_sec=parse_time(m.group("end_h"), m.group("end_m"), m.group("end_s")),
    )


def parse_qty_range(qty_str: str) -> tuple[int, int]:
    numbers = [int(x) for x in re.findall(r'\d+', qty_str)]
    if not numbers:
        return 0, 0
    return min(numbers), max(numbers)


def find_latest_all_days_file() -> Path | None:
    candidates = list(OUTPUT_DIR.glob("signals_all_days_*.txt"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Универсальный анализатор сигналов")
    parser.add_argument("--input", type=str, help="Входной файл (по умолчанию последний signals_all_days_*.txt)")
    parser.add_argument("--min-repeats", type=int, default=4)
    parser.add_argument("--max-jitter", type=float, default=150.0)
    parser.add_argument("--max-cv", type=float, default=0.5)
    parser.add_argument("--preset", type=str, default=None, help="fast_strict или twap_strict")
    parser.add_argument("--aggregate", action="store_true", help="Вывести агрегированный отчёт")
    parser.add_argument("--top", type=int, default=100, help="Число строк в агрегированном отчёте (по умолчанию 100)")
    args = parser.parse_args()

    if args.input:
        input_path = Path(args.input)
    else:
        if not OUTPUT_DIR.exists():
            print("Папка output/ не найдена")
            return
        input_path = find_latest_all_days_file()
        if input_path is None:
            print("Не найдены файлы signals_all_days_*.txt")
            return

    if not input_path.exists():
        print(f"Файл {input_path} не найден")
        return

    print(f"Читаю {input_path.name} ...")
    signals = []
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            sig = parse_signal_line(line)
            if sig:
                signals.append(sig)

    print(f"Распознано сигналов: {len(signals)}")

    # Фильтрация
    filtered = []
    for sig in signals:
        if sig.repeats < args.min_repeats:
            continue
        if sig.jitter_ms is not None and sig.jitter_ms > args.max_jitter:
            continue
        if sig.cv_pct is not None and sig.cv_pct > args.max_cv:
            continue
        if args.preset and sig.preset != args.preset:
            continue
        filtered.append(sig)

    print(f"После фильтров: {len(filtered)}")

    if not filtered:
        print("Нет сигналов, удовлетворяющих условиям.")
        return

    if args.aggregate:
        # Группировка по паттернам
        groups = defaultdict(list)
        for sig in filtered:
            rounded_interval = round(sig.interval, 1)
            key = (sig.preset, sig.symbol, sig.side, rounded_interval)
            groups[key].append(sig)

        rows = []
        for (preset, symbol, side, interval), sigs in groups.items():
            count = len(sigs)
            avg_cv = sum(s.cv_pct for s in sigs if s.cv_pct is not None) / max(count, 1)
            min_cv = min((s.cv_pct for s in sigs if s.cv_pct is not None), default=None)
            avg_jitter = sum(s.jitter_ms for s in sigs if s.jitter_ms is not None) / max(count, 1)
            min_repeats = min(s.repeats for s in sigs)
            max_repeats = max(s.repeats for s in sigs)
            qty_min = min(parse_qty_range(s.qty_str)[0] for s in sigs)
            qty_max = max(parse_qty_range(s.qty_str)[1] for s in sigs)
            rows.append({
                "preset": preset,
                "symbol": symbol,
                "side": side,
                "interval": interval,
                "count": count,
                "avg_cv": avg_cv,
                "min_cv": min_cv,
                "avg_jitter": avg_jitter,
                "min_repeats": min_repeats,
                "max_repeats": max_repeats,
                "qty_str": f"{qty_min}-{qty_max}" if qty_min != qty_max else str(qty_min),
            })

        rows.sort(key=lambda r: (r["min_cv"] if r["min_cv"] is not None else 999, -r["count"]))
        rows = rows[:args.top]

        report_lines = []
        report_lines.append("=" * 100)
        report_lines.append(f"АГРЕГИРОВАННЫЙ ОТЧЁТ (топ-{len(rows)})")
        report_lines.append("=" * 100)
        header = (f"{'Пресет':<12} {'Тикер':<6} {'Стор':<5} {'Интерв':>7} "
                  f"{'Повт':>5} {'Ср.CV%':>7} {'Мин.CV%':>8} {'Ср.джит':>8} {'Объём':>12}")
        report_lines.append(header)
        report_lines.append("-" * 100)
        for r in rows:
            min_cv_str = f"{r['min_cv']:.2f}" if r['min_cv'] is not None else "-"
            report_lines.append(
                f"{r['preset']:<12} {r['symbol']:<6} {r['side']:<5} {r['interval']:>7.1f} "
                f"{r['count']:>5} {r['avg_cv']:>7.2f} {min_cv_str:>8} {r['avg_jitter']:>8.0f} {r['qty_str']:>12}"
            )
        report_lines.append("-" * 100)
        report_lines.append(f"Всего групп: {len(rows)}")
        report_text = "\n".join(report_lines)
        output_name = f"aggregate_report_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.txt"
    else:
        # Детальный отчёт
        report_lines = []
        header = (f"{'Пресет':<12} {'Тикер':<6} {'Стор':<5} {'Повт':>4} "
                  f"{'Интервал':>8} {'Джиттер':>8} {'CV%':>6} {'Объём':>12} {'Начало':>8} {'Конец':>8}")
        report_lines.append("=" * 95)
        report_lines.append("ОТЧЁТ О СИГНАЛАХ")
        report_lines.append("=" * 95)
        report_lines.append(header)
        report_lines.append("-" * 95)
        filtered.sort(key=lambda s: (s.preset, s.cv_pct if s.cv_pct is not None else 999999))
        for sig in filtered:
            start_str = f"{int(sig.start_sec//3600):02d}:{int((sig.start_sec%3600)//60):02d}:{int(sig.start_sec%60):02d}"
            end_str = f"{int(sig.end_sec//3600):02d}:{int((sig.end_sec%3600)//60):02d}:{int(sig.end_sec%60):02d}"
            jitter_str = f"{sig.jitter_ms:.0f}" if sig.jitter_ms is not None else "-"
            cv_str = f"{sig.cv_pct:.2f}" if sig.cv_pct is not None else "-"
            report_lines.append(
                f"{sig.preset:<12} {sig.symbol:<6} {sig.side:<5} {sig.repeats:>4} "
                f"{sig.interval:>8.1f} {jitter_str:>8} {cv_str:>6} {sig.qty_str:>12} {start_str:>8} {end_str:>8}"
            )
        report_lines.append("-" * 95)
        report_lines.append(f"Всего строк: {len(filtered)}")
        report_text = "\n".join(report_lines)
        output_name = f"signals_filtered_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.txt"

    print(report_text)

    out_path = OUTPUT_DIR / output_name
    out_path.write_text(report_text, encoding="utf-8")
    print(f"\nОтчёт сохранён: {out_path}")


if __name__ == "__main__":
    main()