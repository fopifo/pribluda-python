"""
Приблуда на python — анализатор логов живого скринера.

Читает output/live_signals_*.txt (или указанный файл), извлекает сигналы
и предупреждения о просрочке, считает статистику по тикерам.

Формат реальных строк лога (см. live_screener.py):
  [10:15:32] НОВЫЙ  [робот-интервал[fast_strict]] SBER buy qty=45 ...
  [10:16:01] ЗАКРЫТ  [робот-интервал[slow_strict]] MAGN sell qty=200 ...
  [10:15:40] ⚠ ПРОСРОЧКА  [робот-интервал[fast_strict]] SBER buy qty=45 ...
"""

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"

SIGNAL_PATTERN = re.compile(
    r'^\[(?P<log_time>\d{2}:\d{2}:\d{2})\]\s+(?P<label>НОВЫЙ|ЗАКРЫТ)\s+'
    r'\[робот-интервал\[(?P<preset>[^\]]+)\]\]\s+(?P<symbol>[A-Z]+)\s+(?P<side>buy|sell)\s+'
    r'qty=(?P<qty>[\d-]+)\s+повторов=(?P<repeats>\d+)\s+'
    r'интервал~(?P<interval>[\d.]+)с\s+'
    r'с (?P<start_time>\d{2}:\d{2}:\d{2}) по (?P<end_time>\d{2}:\d{2}:\d{2}) '
    r'\(длилось (?P<duration>[\d.]+) сек\)$'
)
WARNING_PATTERN = re.compile(
    r'^\[(?P<log_time>\d{2}:\d{2}:\d{2})\]\s+⚠\s+ПРОСРОЧКА\s+'
    r'\[робот-интервал\[(?P<preset>[^\]]+)\]\]\s+(?P<symbol>[A-Z]+)\s+(?P<side>buy|sell)\s+'
    r'qty=(?P<qty>[\d-]+)\s+повторов=(?P<repeats>\d+)\s+'
    r'просрочка (?P<overdue>[\d.]+)с\s+\(ожидался удар в (?P<expected>\d{2}:\d{2}:\d{2}) UTC\)$'
)


def find_latest_log() -> Optional[Path]:
    candidates = sorted(OUTPUT_DIR.glob("live_signals_*.txt"))
    return candidates[-1] if candidates else None


def parse_log_file(filepath: Path) -> Tuple[List[Dict], List[Dict]]:
    signals = []
    warnings = []

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue

            m = SIGNAL_PATTERN.match(line)
            if m:
                signals.append({
                    "label": m.group("label"),
                    "preset": m.group("preset"),
                    "symbol": m.group("symbol"),
                    "side": m.group("side"),
                    "qty": m.group("qty"),
                    "repeats": int(m.group("repeats")),
                    "interval_avg": float(m.group("interval")),
                    "start_time": m.group("start_time"),
                    "end_time": m.group("end_time"),
                    "duration": float(m.group("duration")),
                    "raw": line,
                })
                continue

            w = WARNING_PATTERN.match(line)
            if w:
                warnings.append({
                    "preset": w.group("preset"),
                    "symbol": w.group("symbol"),
                    "side": w.group("side"),
                    "qty": w.group("qty"),
                    "repeats": int(w.group("repeats")),
                    "overdue_sec": float(w.group("overdue")),
                    "expected_time": w.group("expected"),
                    "raw": line,
                })

    return signals, warnings


def compute_statistics(signals: List[Dict], warnings: List[Dict]) -> Dict:
    stats = defaultdict(lambda: {
        "total_signals": 0,
        "repeats_sum": 0,
        "max_repeats": 0,
        "max_duration": 0.0,
        "longest_signal": None,
        "warnings_count": 0,
    })

    for s in signals:
        sym = s["symbol"]
        stats[sym]["total_signals"] += 1
        stats[sym]["repeats_sum"] += s["repeats"]
        if s["repeats"] > stats[sym]["max_repeats"]:
            stats[sym]["max_repeats"] = s["repeats"]
        if s["duration"] > stats[sym]["max_duration"]:
            stats[sym]["max_duration"] = s["duration"]
            stats[sym]["longest_signal"] = s["raw"]

    for w in warnings:
        stats[w["symbol"]]["warnings_count"] += 1

    for data in stats.values():
        data["avg_repeats"] = data["repeats_sum"] / data["total_signals"] if data["total_signals"] else 0.0

    return dict(stats)


def print_report(stats: Dict, log_file: Path) -> None:
    print(f"\n{'=' * 80}")
    print(f"Статистика по логу: {log_file.name}")
    print(f"{'=' * 80}\n")
    print(f"{'Тикер':<8} {'Сигналов':>8} {'Ср.LEN':>8} {'Макс.LEN':>8} {'Макс.длит.,с':>12} {'Предупрежд.':>12}")
    print("-" * 80)
    for sym, data in sorted(stats.items()):
        print(f"{sym:<8} {data['total_signals']:>8} {data['avg_repeats']:>8.1f} "
              f"{data['max_repeats']:>8} {data['max_duration']:>12.1f} {data['warnings_count']:>12}")
    print("-" * 80)
    total_signals = sum(d["total_signals"] for d in stats.values())
    total_warnings = sum(d["warnings_count"] for d in stats.values())
    print(f"Всего тикеров: {len(stats)}")
    print(f"Всего сигналов: {total_signals}")
    print(f"Всего предупреждений: {total_warnings}")

    if stats:
        longest = max(stats.items(), key=lambda x: x[1]["max_duration"])
        if longest[1]["longest_signal"]:
            print(f"\nСамая длинная серия: {longest[0]} — {longest[1]['longest_signal']}")


def save_json_report(stats: Dict, log_file: Path) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    report_path = OUTPUT_DIR / f"analysis_{log_file.stem}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    print(f"\nJSON-отчёт сохранён: {report_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Анализ логов живого скринера")
    parser.add_argument("log_file", nargs="?", help="Путь к файлу лога (если не указан, берётся последний)")
    args = parser.parse_args()

    if args.log_file:
        log_path = Path(args.log_file)
        if not log_path.exists():
            print(f"Файл {log_path} не найден.")
            return
    else:
        log_path = find_latest_log()
        if not log_path:
            print("Не найдено ни одного файла live_signals_*.txt в output/")
            return

    print(f"Анализируем {log_path} ...")
    signals, warnings = parse_log_file(log_path)
    print(f"Найдено сигналов: {len(signals)}, предупреждений: {len(warnings)}")

    stats = compute_statistics(signals, warnings)
    print_report(stats, log_path)
    save_json_report(stats, log_path)


if __name__ == "__main__":
    main()