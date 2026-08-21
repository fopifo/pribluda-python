"""
Приблуда на python — пакетный прогон детекторов по ВСЕМ датам, для
которых в data/ есть файлы {SYMBOL}_{ДАТА}.json.

Поддерживает два источника данных:
1. JSON-файлы data/{SYMBOL}_{ДАТА}.json (старый формат)
2. Quik-лента data/quik_trades.csv (актуальный формат, фильтрует по дате)

Автоматически выбирает: если есть JSON для даты — использует его,
иначе читает Quik-ленту.

Формат вывода совместим с analysis/week_signals_review.py:
строки "ДАТА <дата>" и строки сигналов с отступом.

ПРОГРЕСС: в консоль печатается живой счётчик "[дата i/N] тикер k/M"
(flush=True), чтобы было видно, что скрипт работает, а не "завис".

Полный вывод (все сигналы) пишется только в файл output/signals_all_dates.txt.
Каждый новый прогон перезаписывает предыдущий.

Запуск (из корня проекта):
python research/run_all_dates.py
"""
import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.config import get_detector_configs
from core.ticker_settings import load_settings
from detectors.interval_robot import IntervalRobotDetector

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


def get_active_symbols(settings: dict) -> list[str]:
    """Возвращает список активных тикеров из настроек."""
    return [sym for sym, cfg in settings.items() if cfg.get("active", True)]


def load_trades_json(symbol: str, date: str) -> list[dict] | None:
    """Загружает сделки из JSON-файла."""
    path = DATA_DIR / f"{symbol}_{date}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_trades_quik(symbol: str, date: str) -> list[dict] | None:
    """Загружает сделки из Quik-ленты для символа и даты."""
    csv_path = DATA_DIR / "quik_trades.csv"
    if not csv_path.exists():
        return None
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo
    MSK = ZoneInfo("Europe/Moscow")
    target_date = dt.strptime(date, "%Y-%m-%d").date()
    
    trades = []
    with open(csv_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            parts = line.strip().split(";")
            if len(parts) < 5:
                continue
            if parts[0] != symbol:
                continue
            try:
                ts = int(float(parts[4]))
                trade_dt = dt.fromtimestamp(ts / 1000, tz=MSK)
                if trade_dt.date() != target_date:
                    continue
                trades.append({
                    "symbol": parts[0],
                    "qty": int(float(parts[1])),
                    "price": float(parts[2]),
                    "side": parts[3],
                    "timestamp": ts,
                })
            except (ValueError, IndexError):
                continue
    return trades if trades else None


def load_trades(symbol: str, date: str) -> list[dict] | None:
    """Загружает сделки: сначала пробует JSON, если нет — Quik-ленту."""
    trades = load_trades_json(symbol, date)
    if trades is not None:
        return trades
    return load_trades_quik(symbol, date)


def run_detectors_on_trades(detectors: list[IntervalRobotDetector], trades: list[dict]) -> list:
    """Прогоняет сделки через детекторы, возвращает сигналы."""
    signals = []
    for t in trades:
        for d in detectors:
            signals.extend(d.on_trade(t))
    for d in detectors:
        signals.extend(d.flush())
    return signals


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
            trades = load_trades(symbol, date)
            if not trades:
                continue
            
            override = settings.get(symbol, {})
            manual = override.get("min_qty")
            min_qty = manual if manual is not None else 10  # дефолт
            configs = get_detector_configs(symbol, min_qty, override)
            detectors = [IntervalRobotDetector(symbol, cfg) for cfg in configs]
            signals = run_detectors_on_trades(detectors, trades)
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