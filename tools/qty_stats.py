"""
Приблуда на python — статистика объёмов сделок по тикерам (диагностика).

Не влияет на работу детекторов — run_detectors.py и live_screener.py
считают свой порог min_qty сами. Этот скрипт — ручной инструмент,
чтобы глазами посмотреть на распределение объёмов.
"""

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import TRACKED_SYMBOLS
from stats import percentile

DATA_DIR = BASE_DIR / "data"
PERCENTILES = [50, 75, 90, 95, 99]


def find_latest_file(symbol: str) -> Path | None:
    candidates = sorted(DATA_DIR.glob(f"{symbol}_*.json"))
    return candidates[-1] if candidates else None


def main() -> None:
    header = "TICKER".ljust(8) + "СДЕЛОК".rjust(8)
    for p in PERCENTILES:
        header += f"  p{p}".rjust(8)
    print(header)

    for symbol in TRACKED_SYMBOLS:
        data_file = find_latest_file(symbol)
        if data_file is None:
            print(f"{symbol.ljust(8)}  файл не найден")
            continue

        with open(data_file, encoding="utf-8") as f:
            trades = json.load(f)

        qtys = sorted(t["qty"] for t in trades)
        row = symbol.ljust(8) + str(len(qtys)).rjust(8)
        for p in PERCENTILES:
            row += str(percentile(qtys, p)).rjust(10)
        print(row)


if __name__ == "__main__":
    main()