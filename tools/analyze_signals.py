"""
Приблуда на python — выгрузка значимых завершённых сигналов из логов
или исторических отчётов.

Читает файлы сигналов в output/ (или указанные явно), игнорирует
предупреждения о просрочке и служебные заголовки, извлекает строки
НОВЫЙ/ЗАКРЫТ и формирует компактный отчёт.

Поддерживаются два формата строк:
  1. Живой лог: [HH:MM:SS] НОВЫЙ/ЗАКРЫТ [робот-интервал[...]] ...
  2. Исторический отчёт: [робот-интервал[...]] ... (без времени в начале,
     зато могут быть ведущие пробелы)

Фильтры (можно менять в начале):
  MIN_REPEATS   — минимальное число повторов (по умолчанию 4)
  MAX_JITTER_MS — максимальный джиттер (по умолчанию 150 мс)
  MAX_CV_PCT    — максимальный коэффициент вариации, % (по умолчанию 2.0)
  PRESET_FILTER — если задан, показывать только этот пресет
                  (например, "twap_strict" или "fast_strict")

Запуск:
    python tools/analyze_signals.py [файл1 файл2 ...]

Без аргументов ищет в output/ файлы live_signals_*.txt и signals_*.txt
(включая signals_all_days_*.txt), выбирает последний из них по дате
создания и анализирует его.

Результат печатается в консоль и сохраняется в output/signals_report_*.txt.
"""

import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"

# ----- НАСТРОЙКИ ФИЛЬТРОВ -----
MIN_REPEATS = 4          # минимум повторов для сигнала
MAX_JITTER_MS = 150.0    # максимум джиттера в мс
MAX_CV_PCT = 2.0         # максимум коэффициента вариации, %
PRESET_FILTER = None     # например: "twap_strict" или "fast_strict"

# Паттерн для строк с меткой времени в начале (живой лог)
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

# Паттерн для строк без метки времени (исторический отчёт)
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
    # Сначала пробуем с меткой времени, потом без
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


def find_latest_signal_file() -> Path | None:
    """Ищем последний файл сигналов в output/ по времени создания."""
    patterns = ["live_signals_*.txt", "signals_*.txt"]
    candidates = []
    for pattern in patterns:
        candidates.extend(OUTPUT_DIR.glob(pattern))
    if not candidates:
        return None
    # Сортируем по времени последнего изменения (сначала новые)
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def main() -> None:
    if len(sys.argv) > 1:
        files = [Path(arg) for arg in sys.argv[1:]]
    else:
        if not OUTPUT_DIR.exists():
            print(f"Папка {OUTPUT_DIR} не найдена. Укажите файлы явно.")
            return
        latest = find_latest_signal_file()
        if latest is None:
            print("В output/ нет файлов live_signals_*.txt или signals_*.txt")
            return
        files = [latest]
        print(f"Анализирую последний файл: {latest.name}")

    signals = []
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
                    signals.append(sig)

    print(f"Всего распознано сигналов: {len(signals)}")

    filtered = []
    for sig in signals:
        if sig.repeats < MIN_REPEATS:
            continue
        if sig.jitter_ms is not None and sig.jitter_ms > MAX_JITTER_MS:
            continue
        if sig.cv_pct is not None and sig.cv_pct > MAX_CV_PCT:
            continue
        if PRESET_FILTER and sig.preset != PRESET_FILTER:
            continue
        filtered.append(sig)

    print(f"После фильтров (min_repeats={MIN_REPEATS}, max_jitter={MAX_JITTER_MS}мс, max_cv={MAX_CV_PCT}%): {len(filtered)}")

    report_lines = []
    header = (f"{'Пресет':<12} {'Тикер':<6} {'Стор':<5} {'Повт':>4} "
              f"{'Интервал':>8} {'Джиттер':>8} {'CV%':>6} {'Объём':>12} {'Начало':>8} {'Конец':>8}")
    report_lines.append("=" * 95)
    report_lines.append("ОТЧЁТ О ЗАВЕРШЁННЫХ СИГНАЛАХ")
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

    print(report_text)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    report_path = OUTPUT_DIR / f"signals_report_{timestamp}.txt"
    report_path.write_text(report_text, encoding="utf-8")
    print(f"\nОтчёт сохранён: {report_path}")


if __name__ == "__main__":
    main()