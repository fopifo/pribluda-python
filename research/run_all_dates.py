"""
Приблуда на python — пакетный прогон детекторов по ВСЕМ датам, для
которых в data/ есть файлы {SYMBOL}_{ДАТА}.json. Нужен для A/B-сравнения
правок детектора: старый недельный журнал (data_sample/Week_trades.txt)
сравнивается с новым прогоном тех же дат на новом коде.
Формат вывода совместим с analysis/week_signals_review.py:
строки "ДАТА <дата>" и строки сигналов с отступом — обзорный скрипт
распарсит и сожмёт в компактный отчёт.
ПРОГРЕСС: в консоль печатается живой счётчик "[дата i/N] тикер k/M"
(flush=True), чтобы было видно, что скрипт работает, а не "завис".
Полный вывод (все сигналы) пишется только в файл.
НАКОПЛЕНИЕ: вывод пишется в ОДИН файл output/signals_all_dates.txt —
каждый новый прогон перезаписывает предыдущий.
Запуск (из корня проекта):
python analysis/run_all_dates.py
"""
import json
import re
import sys
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
OUT_PATH = OUTPUT_DIR / "signals_all_dates.txt"
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

    def flog(line: str = "") -> None:
        lines.append(line)

    settings = load_settings()
    symbols = get_active_symbols(settings)
    dates = find_dates()
    print(f"Активных тикеров: {len(symbols)}", flush=True)
    print(f"Найдено дат в data/: {len(dates)}", flush=True)
    flog(f"Активных тикеров: {len(symbols)}")
    flog(f"Найдено дат в data/: {len(dates)}")

    total_dates = len(dates)
    total_symbols = len(symbols)
    for di, date in enumerate(dates, 1):
        flog("=" * 60)
        flog(f"ДАТА {date}")
        flog("=" * 60)
        day_signals = 0
        for si, symbol in enumerate(symbols, 1):
            print(
                f"\r[{di}/{total_dates}] {date}: тикер {si}/{total_symbols} {symbol}      ",
                end="", flush=True,
            )
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
            day_signals += len(signals)
            flog(f"{symbol}: найдено сигналов: {len(signals)}")
            for s in signals:
                flog(f"  {s}")
        flog("")
        print(f"\r[{di}/{total_dates}] {date}: готово, сигналов за день: {day_signals}      ",
              flush=True)

    OUTPUT_DIR.mkdir(exist_ok=True)
    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Полный вывод сохранён в {OUT_PATH}")


if __name__ == "__main__":
    main()