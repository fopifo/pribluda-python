"""
Приблуда на python — снапшот роботов на момент времени.

Поддерживает два источника данных:
1. JSON-файлы data/{SYMBOL}_{ДАТА}.json (старый формат)
2. Quik-лента data/quik_trades.csv (актуальный формат)

Автоматически выбирает: если есть JSON для даты — использует его,
иначе читает Quik-ленту и фильтрует по дате.

Режимы:
  обычный: python research/robot_snapshot.py 2026-08-17 11:34
  loose  : ... 11:34 loose  — эксперимент: min_qty=1, close_after_misses=5,
           interval_tolerance=0.2, печатает ТОЛЬКО целевые тикеры (земля).

Цель loose — проверить, появляются ли роботы конкурента с высоким LEN,
если снять пороги. Если да — фиксируем пороги.
"""
import sys, json
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from core.config import get_detector_configs
from core.ticker_settings import load_settings
from detectors.interval_robot import IntervalRobotDetector

DATA = BASE / "data"
OUT = BASE / "output"
MIN_REP = 5

TARGETS = {
    ("buy", "RUAL"), ("buy", "BSPB"), ("buy", "OZON"),
    ("sell", "X5"), ("sell", "MDNG"), ("sell", "SNGSP"),
    ("sell", "TRNFP"), ("sell", "LKOH"), ("sell", "SBER"),
}


def get_active_symbols(settings: dict) -> list[str]:
    """Возвращает список активных тикеров из настроек."""
    return [sym for sym, cfg in settings.items() if cfg.get("active", True)]


def load_trades_json(symbol: str, date: str) -> list[dict] | None:
    """Загружает сделки из JSON-файла."""
    path = DATA / f"{symbol}_{date}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_trades_quik(symbol: str, date: str) -> list[dict] | None:
    """Загружает сделки из Quik-ленты для символа и даты."""
    csv_path = DATA / "quik_trades.csv"
    if not csv_path.exists():
        return None
    from zoneinfo import ZoneInfo
    MSK = ZoneInfo("Europe/Moscow")
    target_date = datetime.strptime(date, "%Y-%m-%d").date()
    
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
                trade_dt = datetime.fromtimestamp(ts / 1000, tz=MSK)
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


def main():
    if len(sys.argv) < 3:
        print("Использование: python research/robot_snapshot.py 2026-08-17 11:34 [loose]")
        return
    date, hm = sys.argv[1], sys.argv[2]
    loose = len(sys.argv) > 3 and sys.argv[3] == "loose"
    T = datetime.strptime(f"{date} {hm}", "%Y-%m-%d %H:%M").timestamp()
    settings = load_settings()

    rows = []
    for symbol in get_active_symbols(settings):
        if loose and not any(t[1] == symbol for t in TARGETS):
            continue
        
        trades = load_trades(symbol, date)
        if not trades:
            continue
        
        override = settings.get(symbol, {})
        manual = override.get("min_qty")
        min_qty = manual if manual is not None else 10  # дефолт
        dets = [IntervalRobotDetector(symbol, c) for c in get_detector_configs(symbol, min_qty, override)]
        if loose:
            for d in dets:
                d.min_qty = 1
                d.close_after_misses = 5  # ИСПРАВЛЕНО: было CLOSE_AFTER_MISSES
                d.interval_tolerance = 0.2
        for t in trades:
            if t["timestamp"] / 1000 > T:
                break
            for d in dets:
                d.on_trade(t)
        for d in dets:
            for row in d.get_active_snapshot(T):
                if loose:
                    if (row["side"], row["symbol"]) in TARGETS and row["repeats"] >= 3:
                        row["_preset"] = d.preset_name
                        rows.append(row)
                elif row["repeats"] >= MIN_REP:
                    row["_preset"] = d.preset_name
                    rows.append(row)

    if loose:
        print("LOOSE MODE (min_qty=1, misses=5, tol=0.2) — только цели")
        print(f"{'SIDE':5} {'TICKER':7} {'QTY':14} {'INT':6} {'LEN':4} preset")
        for r in sorted(rows, key=lambda r: (r["symbol"], -r["repeats"])):
            qty = "-".join(str(q) for q in r["qty_variants"])
            int_s = f"{r['interval']:.0f}s" if r["interval"] else "-"
            print(f"{r['side']:5} {r['symbol']:7} {qty:14} {int_s:6} {r['repeats']:4} {r['_preset']}")
        print(f"-- target series LEN>=3: {len(rows)}")
    else:
        print(f"{'SIDE':5} {'TICKER':7} {'QTY':12} {'INT':6} {'LEN':4} preset")
        for r in sorted(rows, key=lambda r: (r["symbol"], r["side"], -r["repeats"])):
            qty = "-".join(str(q) for q in r["qty_variants"])
            int_s = f"{r['interval']:.0f}s" if r["interval"] else "-"
            print(f"{r['side']:5} {r['symbol']:7} {qty:12} {int_s:6} {r['repeats']:4} {r['_preset']}")
        print(f"-- series LEN>={MIN_REP}: {len(rows)}")


if __name__ == "__main__":
    main()