"""
Приблуда на python — анализ логов живого скринера на предмет
параллельных сигналов: несколько сигналов по одному тикеру и стороне
с одинаковыми/близкими временами начала/конца и интервалом, но с
разными объёмами. Такие паттерны могут указывать на TWAP-робота с
разбиением объёма.

Запуск:
    python analyze_parallel.py [файл1 файл2 ...]

Без аргументов анализирует все live_signals_*.txt в output/.
Результат печатается в консоль и сохраняется в output/parallel_report_*.txt.
"""

import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"

# Допуски для группировки (можно менять)
START_TOLERANCE = 2.0      # сек, допустимая разница во времени начала
END_TOLERANCE = 2.0        # сек, допустимая разница во времени окончания
INTERVAL_TOLERANCE_FACTOR = 0.10  # 10% допустимой разницы интервалов

# ВАЖНО: тикер может содержать цифры (например, X5) — символьный класс
# [A-Z0-9]+ вместо [A-Z]+, иначе такие строки молча выпадают из анализа
# (было найдено на реальном логе: 93 строки по X5 не распознавались).
SIGNAL_PATTERN = re.compile(
    r'^\[(?P<log_time>\d{2}:\d{2}:\d{2})\]\s+(?P<label>НОВЫЙ|ЗАКРЫТ)\s+'
    r'\[робот-интервал\[(?P<preset>[^\]]+)\]\]\s+(?P<symbol>[A-Z0-9]+)\s+(?P<side>buy|sell)\s+'
    r'qty=(?P<qty>[\d-]+)\s+повторов=(?P<repeats>\d+)\s+'
    r'интервал~(?P<interval>[\d.]+)с\s+'
    r'(?:джиттер=[\d.]+мс\s+)?'   # опциональная часть
    r'с (?P<start_h>\d{2}):(?P<start_m>\d{2}):(?P<start_s>\d{2}) '
    r'по (?P<end_h>\d{2}):(?P<end_m>\d{2}):(?P<end_s>\d{2}) '
    r'\(длилось [\d.]+ сек\)$'
)


@dataclass
class Signal:
    symbol: str
    side: str
    qty: int
    interval: float
    start_sec: float
    end_sec: float


def parse_time(h: str, m: str, s: str) -> float:
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_signal_line(line: str) -> Signal | None:
    m = SIGNAL_PATTERN.match(line)
    if not m:
        return None
    qty_str = m.group("qty")
    if "-" in qty_str:
        # Пока в проекте включён только fast_strict (объём всегда один,
        # без чередования), дефис в qty на практике не встречается.
        # Если когда-нибудь снова включим *_loose с чередованием
        # объёма — суммирование здесь ("45-46" -> 91) станет вводить в
        # заблуждение (это чередование, а не объём 91) — тогда нужно
        # будет пересмотреть эту логику.
        qty = sum(int(x) for x in qty_str.split("-"))
    else:
        qty = int(qty_str)
    interval = float(m.group("interval"))
    start_sec = parse_time(m.group("start_h"), m.group("start_m"), m.group("start_s"))
    end_sec = parse_time(m.group("end_h"), m.group("end_m"), m.group("end_s"))
    return Signal(
        symbol=m.group("symbol"),
        side=m.group("side"),
        qty=qty,
        interval=interval,
        start_sec=start_sec,
        end_sec=end_sec,
    )


def find_parallel_groups(signals: list[Signal]) -> list[list[Signal]]:
    """Группирует сигналы по (symbol, side) и находит кластеры параллельных."""
    by_key = defaultdict(list)
    for sig in signals:
        by_key[(sig.symbol, sig.side)].append(sig)

    parallel_groups = []
    for (symbol, side), sigs in by_key.items():
        sigs.sort(key=lambda s: s.start_sec)
        n = len(sigs)
        used = [False] * n
        for i in range(n):
            if used[i]:
                continue
            cluster = [sigs[i]]
            used[i] = True
            changed = True
            while changed:
                changed = False
                avg_start = sum(s.start_sec for s in cluster) / len(cluster)
                avg_end = sum(s.end_sec for s in cluster) / len(cluster)
                avg_interval = sum(s.interval for s in cluster) / len(cluster)
                for j in range(n):
                    if used[j]:
                        continue
                    candidate = sigs[j]
                    if (abs(candidate.start_sec - avg_start) <= START_TOLERANCE and
                        abs(candidate.end_sec - avg_end) <= END_TOLERANCE and
                        abs(candidate.interval - avg_interval) <= INTERVAL_TOLERANCE_FACTOR * avg_interval):
                        cluster.append(candidate)
                        used[j] = True
                        changed = True
            if len(cluster) >= 2:
                parallel_groups.append(cluster)
    return parallel_groups


def main() -> None:
    if len(sys.argv) > 1:
        files = [Path(arg) for arg in sys.argv[1:]]
    else:
        if not OUTPUT_DIR.exists():
            print(f"Папка {OUTPUT_DIR} не найдена. Укажите файлы явно.")
            return
        files = sorted(OUTPUT_DIR.glob("live_signals_*.txt"))
        if not files:
            print("Нет файлов live_signals_*.txt в output/")
            return

    all_signals = []
    for file_path in files:
        if not file_path.exists():
            print(f"Файл {file_path} не найден, пропускаю.")
            continue
        print(f"Читаю {file_path.name} ...")
        with open(file_path, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip()
                if not line:
                    continue
                sig = parse_signal_line(line)
                if sig:
                    all_signals.append(sig)

    print(f"Всего распознано сигналов (НОВЫЙ/ЗАКРЫТ): {len(all_signals)}")

    if not all_signals:
        print("Не найдено ни одного сигнала.")
        return

    groups = find_parallel_groups(all_signals)
    print(f"Найдено групп параллельных сигналов (>=2): {len(groups)}")

    report_lines = []
    header = f"{'Тикер':<6} {'Время':<12} {'Число':<6} {'Сумм. объём':<12} {'Ср. интервал':<12}"
    report_lines.append("=" * 70)
    report_lines.append("ОТЧЁТ О ПАРАЛЛЕЛЬНЫХ СИГНАЛАХ")
    report_lines.append("=" * 70)
    report_lines.append(header)
    report_lines.append("-" * 70)

    groups.sort(key=lambda g: min(s.start_sec for s in g))

    for group in groups:
        symbol = group[0].symbol
        start_sec_min = min(s.start_sec for s in group)
        h = int(start_sec_min // 3600)
        m = int((start_sec_min % 3600) // 60)
        s = int(start_sec_min % 60)
        time_str = f"{h:02d}:{m:02d}:{s:02d}"
        count = len(group)
        total_qty = sum(sig.qty for sig in group)
        avg_interval = sum(sig.interval for sig in group) / count
        report_lines.append(f"{symbol:<6} {time_str:<12} {count:<6} {total_qty:<12} {avg_interval:<12.2f}")

    report_lines.append("-" * 70)
    report_lines.append(f"Всего групп: {len(groups)}")

    report_text = "\n".join(report_lines)
    print(report_text)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    report_path = OUTPUT_DIR / f"parallel_report_{timestamp}.txt"
    report_path.write_text(report_text, encoding="utf-8")
    print(f"\nОтчёт сохранён в {report_path}")


if __name__ == "__main__":
    main()