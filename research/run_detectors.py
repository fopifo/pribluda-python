"""
Приблуда на python — запуск детекторов на сохранённых лентах сделок
по нескольким тикерам сразу.

Поддерживает два источника данных:
1. JSON-файлы data/{SYMBOL}_{ДАТА}.json (старый формат, скачанные заранее)
2. Quik-лента data/quik_trades.csv (актуальный формат, собирается lua-скриптом)

Автоматически выбирает источник: если есть JSON для последней даты —
использует его, иначе — читает Quik-ленту.

Список тикеров и их активность берутся из core.ticker_settings.
Порог min_qty: если задан вручную в ticker_settings.json — используется,
иначе вычисляется из фактического распределения объёмов (p50 по умолчанию).

Весь вывод печатается в консоль И сохраняется в output/signals_<дата>_<время>.txt.
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.config import get_detector_configs
from core.ticker_settings import load_settings
from detectors.interval_robot import IntervalRobotDetector

DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"

LogFunc = Callable[[str], None]


def qty_percentile(trades: list[dict], pct: float) -> int:
    """Процентиль по объёму сделок. pct в процентах (0-100)."""
    if not trades:
        return 1
    qtys = sorted(t["qty"] for t in trades if "qty" in t)
    if not qtys:
        return 1
    idx = int(len(qtys) * pct / 100)
    return qtys[min(idx, len(qtys) - 1)]


def find_latest_json(symbol: str) -> Path | None:
    """Находит самый свежий JSON-файл для символа."""
    candidates = sorted(DATA_DIR.glob(f"{symbol}_*.json"))
    return candidates[-1] if candidates else None


def load_trades_json(symbol: str, log: LogFunc) -> list[dict] | None:
    """Загружает сделки из JSON-файла."""
    data_file = find_latest_json(symbol)
    if data_file is None:
        log(f"  Файлы не найдены для {symbol} (искал {DATA_DIR}/{symbol}_*.json)")
        return None
    with open(data_file, encoding="utf-8") as f:
        trades = json.load(f)
    log(f"{symbol}: загружено сделок: {len(trades)} (файл {data_file.name})")
    return trades


def load_trades_quik(symbol: str, log: LogFunc) -> list[dict] | None:
    """Загружает сделки из Quik-ленты (data/quik_trades.csv) для символа."""
    csv_path = DATA_DIR / "quik_trades.csv"
    if not csv_path.exists():
        log(f"  Quik-лента не найдена: {csv_path}")
        return None
    trades = []
    with open(csv_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            parts = line.strip().split(";")
            if len(parts) < 5:
                continue
            if parts[0] != symbol:
                continue
            try:
                trades.append({
                    "symbol": parts[0],
                    "qty": int(float(parts[1])),
                    "price": float(parts[2]),
                    "side": parts[3],
                    "timestamp": int(float(parts[4])),  # уже в мс
                })
            except (ValueError, IndexError):
                continue
    if not trades:
        log(f"  В Quik-ленте нет сделок для {symbol}")
        return None
    log(f"{symbol}: загружено сделок: {len(trades)} (из {csv_path.name})")
    return trades


def load_trades(symbol: str, log: LogFunc) -> list[dict] | None:
    """Загружает сделки: сначала пробует JSON, если нет — Quik-ленту."""
    trades = load_trades_json(symbol, log)
    if trades is not None:
        return trades
    return load_trades_quik(symbol, log)


def resolve_min_qty(symbol: str, override: dict, trades: list[dict], log: LogFunc) -> int:
    """Определяет min_qty: ручной из настроек или автоматический (p50)."""
    manual = override.get("min_qty")
    if manual is not None:
        log(f"{symbol}: min_qty = {manual} лотов (задано вручную в ticker_settings.json)")
        return manual
    pct = 50  # дефолтный процентиль
    min_qty = qty_percentile(trades, pct)
    log(f"{symbol}: min_qty = {min_qty} лотов (p{pct:.0f} объёма за этот день)")
    return min_qty


def run_detectors_on_trades(detectors: list[IntervalRobotDetector], trades: list[dict]) -> list:
    """Прогоняет сделки через детекторы, возвращает сигналы."""
    signals = []
    for t in trades:
        for d in detectors:
            signals.extend(d.on_trade(t))
    for d in detectors:
        signals.extend(d.flush())
    return signals


def run_for_symbol(symbol: str, override: dict, log: LogFunc) -> None:
    trades = load_trades(symbol, log)
    if trades is None:
        return

    min_qty = resolve_min_qty(symbol, override, trades, log)
    configs = get_detector_configs(symbol, min_qty, override)
    detectors = [IntervalRobotDetector(symbol, cfg) for cfg in configs]

    signals = run_detectors_on_trades(detectors, trades)
    log(f"{symbol}: найдено сигналов: {len(signals)}")
    for s in signals:
        log(f"  {s}")
    log("")


def get_active_symbols(settings: dict) -> list[str]:
    """Возвращает список активных тикеров из настроек."""
    return [sym for sym, cfg in settings.items() if cfg.get("active", True)]


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