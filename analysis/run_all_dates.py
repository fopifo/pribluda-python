"""
Приблуда на python — пакетный прогон детекторов по ВСЕМ датам, для
которых в data/ есть файлы {SYMBOL}_{ДАТА}.json. Нужен для A/B-сравнения
правок детектора: старый недельный журнал (data_sample/Week_trades.txt)
сравнивается с новым прогоном тех же дат на новом коде.
Формат вывода совместим с analysis/week_signals_review.py:
строки "ДАТА <дата>" и строки сигналов с отступом — обзорный скрипт
распарсит и сожмёт в компактный отчёт.
Запуск (из корня проекта):
python analysis/run_all_dates.py
Может идти несколько минут (все тикеры × все даты).
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import get_detector_configs, get_min_qty_percentile
from detectors.interval_robot import IntervalRobotDetector
from engine import TradeBuffer
from stats import qty_percentile
from ticker_settings import get_active_symbols, load_settings

DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DATE_RE = re.compile(r"_(\d{4}-\d{2}-\d{2})\.json$")


def find_dates() -> list[str]:
    dates = set()
    for path in DATA_DIR.glob("*_*.json"):
        m = DATE_RE.search(path.name)
        if m:
            dates.add(m.group(1))
    return sorted(dates)


def main() -> None:
    lines: list[str] = []

    def log(line: str = "") -> None:
        print(line)
        lines.append(line)

    settings = load_settings()
    symbols = get_active_symbols(settings)
    dates = find_dates()
    log(f"Активных тикеров: {len(symbols)}")
    log(f"Найдено дат в data/: {len(dates)}")

    for date in dates:
        log("=" * 60)
        log(f"ДАТА {date}")
        log("=" * 60)
        for symbol in symbols:
            path = DATA_DIR / f"{symbol}_{date}.json"
            if not path.exists():
                continue
            with open(path, encoding="utf-8") as f:
                trades = json.load(f)
            override = settings.get(symbol, {})
            manual = override.get("min_qty")
            if manual is not None:
                min_qty = manual
            else:
                min_qty = qty_percentile(trades, get_min_qty_percentile(symbol))
            configs = get_detector_configs(symbol, min_qty, override)
            detectors = [IntervalRobotDetector(symbol, cfg) for cfg in configs]
            signals = TradeBuffer(symbol, detectors).process(trades)
            log(f"{symbol}: найдено сигналов: {len(signals)}")
            for s in signals:
                log(f"  {s}")
        log("")

    OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_path = OUTPUT_DIR / f"signals_all_dates_{timestamp}.txt"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Полный вывод сохранён в {out_path}")


if __name__ == "__main__":
    main()