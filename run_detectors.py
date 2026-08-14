"""
Приблуда на python — запуск детекторов на сохранённых лентах сделок
по нескольким тикерам сразу.

Ожидает файлы вида data/{SYMBOL}_{ДАТА}.json — по одному на тикер,
скачанные заранее через save_trades.py.

По умолчанию для каждого тикера берётся файл с самой свежей датой.
Если указать --all-days, то прогон будет выполнен для КАЖДОЙ даты,
найденной в файлах data/{SYMBOL}_*.json, и результаты будут
сгруппированы по датам в выходном отчёте.

Список тикеров и их активность (мониторим/нет) берутся из
ticker_settings.json (см. ticker_settings.py) — отключённые тикеры
просто пропускаются.

Порог min_qty (в лотах): если у тикера в ticker_settings.json задан
ручной min_qty — используется как есть. Если нет — вычисляется заново
при каждом запуске из фактического распределения объёмов сделок ЭТОГО
дня по ЭТОМУ тикеру (см. config.py — min_qty_percentile) — подстраивается
под текущую ликвидность, а не берётся фиксированным числом.

Весь вывод одновременно печатается в консоль И сохраняется в файл
output/signals_<дата>_<время>.txt (или signals_all_days_... при
--all-days).
"""

import json
import sys
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


def find_all_data_dates() -> list[str]:
    """Возвращает отсортированный список уникальных дат (в формате
    YYYY-MM-DD), встречающихся в именах файлов data/*_*.json."""
    dates = set()
    for file_path in DATA_DIR.glob("*_*.json"):
        # Имя файла: SYMBOL_YYYY-MM-DD.json
        stem = file_path.stem
        parts = stem.rsplit("_", 1)
        if len(parts) == 2:
            date_str = parts[1]
            dates.add(date_str)
    return sorted(dates)


def load_trades_from_file(file_path: Path, log: LogFunc) -> list[dict] | None:
    if not file_path.exists():
        return None
    with open(file_path, encoding="utf-8") as f:
        trades = json.load(f)
    log(f"    {file_path.name}: загружено сделок: {len(trades)}")
    return trades


def resolve_min_qty(symbol: str, override: dict, trades: list[dict], log: LogFunc) -> int:
    manual = override.get("min_qty")
    if manual is not None:
        log(f"    {symbol}: min_qty = {manual} лотов (задано вручную в ticker_settings.json)")
        return manual
    pct = get_min_qty_percentile(symbol)
    min_qty = qty_percentile(trades, pct)
    log(f"    {symbol}: min_qty = {min_qty} лотов (p{pct:.0f} объёма за этот день)")
    return min_qty


def run_for_symbol_on_file(symbol: str, override: dict, file_path: Path, log: LogFunc) -> None:
    trades = load_trades_from_file(file_path, log)
    if trades is None:
        log(f"    Файл не найден для {symbol}: {file_path}")
        return

    min_qty = resolve_min_qty(symbol, override, trades, log)

    configs = get_detector_configs(symbol, min_qty, override)
    detectors = [IntervalRobotDetector(symbol, cfg) for cfg in configs]

    buffer = TradeBuffer(symbol, detectors)
    signals = buffer.process(trades)

    log(f"    {symbol}: найдено сигналов: {len(signals)}")
    for s in signals:
        log(f"      {s}")
    log("")


def run_for_symbol_latest(symbol: str, override: dict, log: LogFunc) -> None:
    """Прогон только по самому свежему файлу для тикера (как раньше)."""
    data_file = find_latest_file(symbol)
    if data_file is None:
        log(f"  Файлы не найдены для {symbol} (искал {DATA_DIR}/{symbol}_*.json)")
        return
    log(f"{symbol}: использую файл {data_file.name}")
    run_for_symbol_on_file(symbol, override, data_file, log)


def main() -> None:
    output_lines: list[str] = []

    def log(line: str = "") -> None:
        print(line)
        output_lines.append(line)

    all_days_mode = "--all-days" in sys.argv

    settings = load_settings()
    symbols = get_active_symbols(settings)
    log(f"Активных тикеров: {len(symbols)} (отключённые в ticker_settings.json пропускаются)")
    log("")

    if all_days_mode:
        # Режим: прогоняем все даты, которые есть в data/
        dates = find_all_data_dates()
        log(f"Найдено дат в data/: {len(dates)}")
        for date_str in dates:
            log("=" * 60)
            log(f"ДАТА {date_str}")
            log("=" * 60)
            for symbol in symbols:
                file_path = DATA_DIR / f"{symbol}_{date_str}.json"
                log(f"{symbol}: файл {file_path.name}")
                run_for_symbol_on_file(symbol, settings.get(symbol, {}), file_path, log)
    else:
        # Обычный режим: только последний файл для каждого тикера
        for symbol in symbols:
            run_for_symbol_latest(symbol, settings.get(symbol, {}), log)

    OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    if all_days_mode:
        result_file = OUTPUT_DIR / f"signals_all_days_{timestamp}.txt"
    else:
        result_file = OUTPUT_DIR / f"signals_{timestamp}.txt"

    with open(result_file, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))

    print(f"\nПолный вывод сохранён в {result_file}")


if __name__ == "__main__":
    main()