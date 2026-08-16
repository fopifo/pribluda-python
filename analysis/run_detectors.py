"""
Приблуда на python — запуск детекторов на сохранённых лентах сделок
по нескольким тикерам сразу.

Ожидает файлы вида data/{SYMBOL}_{ДАТА}.json — по одному на тикер,
скачанные заранее через save_trades.py. Дата в имени файла не хардкодится
здесь: для каждого тикера берётся файл с самой свежей датой из тех, что
реально лежат в data/.

Список тикеров и их активность (мониторим/нет) берутся из
ticker_settings.json (см. ticker_settings.py) — отключённые тикеры
просто пропускаются.

Порог min_qty (в лотах): если у тикера в ticker_settings.json задан
ручной min_qty — используется как есть. Если нет — вычисляется заново
при каждом запуске из фактического распределения объёмов сделок ЭТОГО
дня по ЭТОМУ тикеру (см. config.py — min_qty_percentile) — подстраивается
под текущую ликвидность, а не берётся фиксированным числом.

Весь вывод одновременно печатается в консоль И сохраняется в файл
output/signals_<дата>_<время>.txt.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from config import get_detector_configs, get_min_qty_percentile
from detectors.interval_robot import IntervalRobotDetector
from engine import TradeBuffer
from stats import qty_percentile
from ticker_settings import get_active_symbols, load_settings

DATA_DIR = Path("data")
OUTPUT_DIR = Path("output")

LogFunc = Callable[[str], None]


def find_latest_file(symbol: str) -> Path | None:
    candidates = sorted(DATA_DIR.glob(f"{symbol}_*.json"))
    return candidates[-1] if candidates else None


def load_trades(symbol: str, log: LogFunc) -> list[dict] | None:
    data_file = find_latest_file(symbol)
    if data_file is None:
        log(f"  Файлы не найдены для {symbol} (искал {DATA_DIR}/{symbol}_*.json)")
        return None
    with open(data_file, encoding="utf-8") as f:
        trades = json.load(f)
    log(f"{symbol}: загружено сделок: {len(trades)} (файл {data_file.name})")
    return trades


def resolve_min_qty(symbol: str, override: dict, trades: list[dict], log: LogFunc) -> int:
    manual = override.get("min_qty")
    if manual is not None:
        log(f"{symbol}: min_qty = {manual} лотов (задано вручную в ticker_settings.json)")
        return manual
    pct = get_min_qty_percentile(symbol)
    min_qty = qty_percentile(trades, pct)
    log(f"{symbol}: min_qty = {min_qty} лотов (p{pct:.0f} объёма за этот день)")
    return min_qty


def run_for_symbol(symbol: str, override: dict, log: LogFunc) -> None:
    trades = load_trades(symbol, log)
    if trades is None:
        return

    min_qty = resolve_min_qty(symbol, override, trades, log)

    configs = get_detector_configs(symbol, min_qty, override)
    detectors = [IntervalRobotDetector(symbol, cfg) for cfg in configs]

    buffer = TradeBuffer(symbol, detectors)
    signals = buffer.process(trades)

    log(f"{symbol}: найдено сигналов: {len(signals)}")
    for s in signals:
        log(f"  {s}")
    log("")


def main() -> None:
    output_lines: list[str] = []

    def log(line: str = "") -> None:
        print(line)
        output_lines.append(line)

    settings = load_settings()
    symbols = get_active_symbols(settings)
    log(f"Активных тикеров: {len(symbols)} (отключённые в ticker_settings.json пропускаются)")
    log("")

    for symbol in symbols:
        run_for_symbol(symbol, settings.get(symbol, {}), log)

    OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    result_file = OUTPUT_DIR / f"signals_{timestamp}.txt"
    with open(result_file, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))

    print(f"\nПолный вывод сохранён в {result_file}")


if __name__ == "__main__":
    main()