"""
Приблуда на python — универсальный анализатор отчётов сигналов.

Запуск без аргументов:
    python tools/analyze.py

Автоматически находит последний signals_all_days_*.txt в output/,
применяет фильтры по умолчанию и выводит двухуровневый агрегированный
отчёт (сводка по тикерам/сторонам + детальные интервальные паттерны).

Если нужен детальный отчёт по каждой строке, используйте:
    python tools/analyze.py --detail

Доступные аргументы:
  --input <файл>       входной файл (по умолчанию последний signals_all_days_*.txt)
  --min-repeats N      минимальное число повторов (по умолчанию 4)
  --max-jitter N       максимальный джиттер, мс (по умолчанию 150)
  --max-cv N           максимальный CV% (по умолчанию 0.5)
  --preset NAME        фильтр по пресету (fast_strict или twap_strict)
  --aggregate          агрегированный отчёт (по умолчанию)
  --detail             детальный отчёт
  --top N              топ N групп в агрегированном отчёте (по умолчанию 50)
  --interval-step S    шаг округления интервала для группировки, сек (по умолчанию 0.5)
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

# Паттерны для строк сигналов (с опциональным временем и ведущими пробелами)
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
        candidates = list(OUTPUT_DIR.glob("signals_*.txt"))
    if not candidates:
        candidates = list(OUTPUT_DIR.glob("live_signals_*.txt"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def weighted_avg_cv(signals: list[SignalRow]) -> float:
    total_weight = 0
    weighted_sum = 0.0
    for s in signals:
        if s.cv_pct is not None:
            weight = s.repeats
            weighted_sum += s.cv_pct * weight
            total_weight += weight
    return weighted_sum / total_weight if total_weight else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Универсальный анализатор сигналов")
    parser.add_argument("--input", type=str, help="Входной файл (по умолчанию последний signals_all_days_*.txt)")
    parser.add_argument("--min-repeats", type=int, default=4)
    parser.add_argument("--max-jitter", type=float, default=150.0)
    parser.add_argument("--max-cv", type=float, default=0.5)
    parser.add_argument("--preset", type=str, default=None, help="fast_strict или twap_strict")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--aggregate", action="store_true", help="Агрегированный отчёт (по умолчанию)")
    group.add_argument("--detail", action="store_true", help="Детальный отчёт")
    parser.add_argument("--top", type=int, default=50, help="Число групп в агрегированном отчёте (по умолчанию 50)")
    parser.add_argument("--interval-step", type=float, default=0.5, help="Шаг округления интервала для группировки, сек (по умолчанию 0.5)")
    args = parser.parse_args()

    # По умолчанию агрегированный режим
    if not args.aggregate and not args.detail:
        args.aggregate = True

    if args.input:
        input_path = Path(args.input)
    else:
        if not OUTPUT_DIR.exists():
            print("Папка output/ не найдена")
            return
        input_path = find_latest_all_days_file()
        if input_path is None:
            print("Не найдены файлы сигналов (signals_all_days_*.txt, signals_*.txt, live_signals_*.txt)")
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

    print(f"После фильтров (min_repeats={args.min_repeats}, max_jitter={args.max_jitter}мс, max_cv={args.max_cv}%): {len(filtered)}")

    if not filtered:
        print("Нет сигналов, удовлетворяющих условиям.")
        return

    if args.detail:
        # Детальный отчёт
        report_lines = []
        header = (f"{'Пресет':<12} {'Тикер':<6} {'Стор':<5} {'Повт':>4} "
                  f"{'Интервал':>8} {'Джиттер':>8} {'CV%':>6} {'Объём':>12} {'Начало':>8} {'Конец':>8}")
        report_lines.append("=" * 95)
        report_lines.append("ДЕТАЛЬНЫЙ ОТЧЁТ О СИГНАЛАХ")
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
    else:
        # Агрегированный отчёт (двухуровневый)
        # Уровень 2: сводка по тикер/сторона/пресет
        summary_groups = defaultdict(list)
        for sig in filtered:
            key = (sig.preset, sig.symbol, sig.side)
            summary_groups[key].append(sig)

        summary_rows = []
        for (preset, symbol, side), sigs in summary_groups.items():
            total_repeats = sum(s.repeats for s in sigs)
            avg_cv = weighted_avg_cv(sigs)
            avg_jitter = sum(s.jitter_ms for s in sigs if s.jitter_ms is not None) / max(len(sigs), 1)
            qty_min = min(parse_qty_range(s.qty_str)[0] for s in sigs)
            qty_max = max(parse_qty_range(s.qty_str)[1] for s in sigs)
            summary_rows.append({
                "preset": preset,
                "symbol": symbol,
                "side": side,
                "signals": len(sigs),
                "total_repeats": total_repeats,
                "avg_cv": avg_cv,
                "avg_jitter": avg_jitter,
                "qty_str": f"{qty_min}-{qty_max}" if qty_min != qty_max else str(qty_min),
            })

        summary_rows.sort(key=lambda r: (r["avg_cv"], -r["total_repeats"]))

        # Уровень 1: группы по интервалам
        interval_groups = defaultdict(list)
        step = args.interval_step
        for sig in filtered:
            rounded_interval = round(sig.interval / step) * step
            key = (sig.preset, sig.symbol, sig.side, rounded_interval)
            interval_groups[key].append(sig)

        interval_rows = []
        for (preset, symbol, side, interval), sigs in interval_groups.items():
            count = len(sigs)
            avg_cv = weighted_avg_cv(sigs)
            min_cv = min((s.cv_pct for s in sigs if s.cv_pct is not None), default=None)
            avg_jitter = sum(s.jitter_ms for s in sigs if s.jitter_ms is not None) / max(count, 1)
            min_repeats = min(s.repeats for s in sigs)
            max_repeats = max(s.repeats for s in sigs)
            qty_min = min(parse_qty_range(s.qty_str)[0] for s in sigs)
            qty_max = max(parse_qty_range(s.qty_str)[1] for s in sigs)
            interval_rows.append({
                "preset": preset,
                "symbol": symbol,
                "side": side,
                "interval": interval,
                "signals": count,
                "total_repeats": sum(s.repeats for s in sigs),
                "avg_cv": avg_cv,
                "min_cv": min_cv,
                "avg_jitter": avg_jitter,
                "min_repeats": min_repeats,
                "max_repeats": max_repeats,
                "qty_str": f"{qty_min}-{qty_max}" if qty_min != qty_max else str(qty_min),
            })

        interval_rows.sort(key=lambda r: (r["min_cv"] if r["min_cv"] is not None else 999, -r["total_repeats"]))
        interval_rows = interval_rows[:args.top]

        # Формируем отчёт
        report_lines = []
        report_lines.append("=" * 110)
        report_lines.append("АГРЕГИРОВАННЫЙ ОТЧЁТ (двухуровневый)")
        report_lines.append("=" * 110)
        report_lines.append("")

        # Уровень 2
        report_lines.append("УРОВЕНЬ 2: СВОДКА ПО ТИКЕРАМ / СТОРОНАМ / ПРЕСЕТАМ")
        report_lines.append("-" * 110)
        header = (f"{'Пресет':<12} {'Тикер':<6} {'Стор':<5} {'Сигн.':>6} "
                  f"{'Сумм.повт':>10} {'Ср.CV%':>8} {'Ср.джит':>8} {'Объём':>14}")
        report_lines.append(header)
        for r in summary_rows[:30]:  # ограничиваем вывод сводки
            report_lines.append(
                f"{r['preset']:<12} {r['symbol']:<6} {r['side']:<5} {r['signals']:>6} "
                f"{r['total_repeats']:>10} {r['avg_cv']:>8.2f} {r['avg_jitter']:>8.0f} {r['qty_str']:>14}"
            )
        report_lines.append("")
        report_lines.append(f"Всего комбинаций в сводке: {len(summary_rows)}")
        report_lines.append("")
        report_lines.append("")

        # Уровень 1
        report_lines.append(f"УРОВЕНЬ 1: ТОП-{len(interval_rows)} ПАТТЕРНОВ ПО ИНТЕРВАЛАМ")
        report_lines.append("-" * 110)
        header = (f"{'Пресет':<12} {'Тикер':<6} {'Стор':<5} {'Интерв':>7} "
                  f"{'Сигн.':>6} {'Сумм.повт':>10} {'Ср.CV%':>7} {'Мин.CV%':>8} {'Ср.джит':>8} {'Объём':>14}")
        report_lines.append(header)
        for r in interval_rows:
            min_cv_str = f"{r['min_cv']:.2f}" if r['min_cv'] is not None else "-"
            report_lines.append(
                f"{r['preset']:<12} {r['symbol']:<6} {r['side']:<5} {r['interval']:>7.1f} "
                f"{r['signals']:>6} {r['total_repeats']:>10} {r['avg_cv']:>7.2f} {min_cv_str:>8} {r['avg_jitter']:>8.0f} {r['qty_str']:>14}"
            )
        report_lines.append("-" * 110)
        report_lines.append(f"Всего групп: {len(interval_rows)}")
        report_text = "\n".join(report_lines)
        output_name = f"aggregate_report_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.txt"

    print(report_text)

    out_path = OUTPUT_DIR / output_name
    out_path.write_text(report_text, encoding="utf-8")
    print(f"\nОтчёт сохранён: {out_path}")


if __name__ == "__main__":
    main()