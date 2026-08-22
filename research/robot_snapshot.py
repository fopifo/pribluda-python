"""
Приблуда на python — снапшот роботов на момент времени.
Поддерживает два источника данных:
1. JSON-файлы data/{SYMBOL}_{ДАТА}.json (старый формат)
2. Quik-лента data/quik_trades.csv (актуальный формат)
Автоматически выбирает: если есть JSON для даты — использует его,
иначе читает Quik-ленту и фильтрует по дате.
v3: ОДИН проход по Quik-ленте вместо чтения всего CSV на каждый тикер
(было 77 чтений подряд — скрипт "висел"); прогресс каждые 200K строк
и по тикерам — видно, что работает, а не упал.
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
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from core.config import get_detector_configs
from core.ticker_settings import load_settings
from detectors.interval_robot import IntervalRobotDetector

DATA = BASE / "data"
OUT = BASE / "output"
MSK = ZoneInfo("Europe/Moscow")
MIN_REP = 5
TARGETS = {
    ("buy", "RUAL"), ("buy", "BSPB"), ("buy", "OZON"),
    ("sell", "X5"), ("sell", "MDNG"), ("sell", "SNGSP"),
    ("sell", "TRNFP"), ("sell", "LKOH"), ("sell", "SBER"),
}


def load_trades_json(symbol: str, date: str) -> list[dict] | None:
    """Загружает сделки из JSON-файла."""
    path = DATA / f"{symbol}_{date}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    if len(sys.argv) < 3:
        print("Использование: python research/robot_snapshot.py 2026-08-17 11:34 [loose]")
        return
    date, hm = sys.argv[1], sys.argv[2]
    loose = len(sys.argv) > 3 and sys.argv[3] == "loose"
    T = datetime.strptime(f"{date} {hm}", "%Y-%m-%d %H:%M").timestamp()
    settings = load_settings()
    active = [sym for sym, cfg in settings.items() if cfg.get("active", True)]
    needed = [s for s in active if any(t[1] == s for t in TARGETS)] if loose else active
    print(f"Режим: {'LOOSE' if loose else 'обычный'}; тикеров: {len(needed)}; момент: {date} {hm}", flush=True)

    # 1) JSON-файлы (если есть для даты) — как раньше
    trades_by_sym = {}
    csv_needed = []
    for sym in needed:
        t = load_trades_json(sym, date)
        if t is not None:
            trades_by_sym[sym] = t
        else:
            csv_needed.append(sym)

    # 2) ОДИН проход по Quik-ленте для остальных (вместо чтения на каждый тикер)
    if csv_needed:
        csv_path = DATA / "quik_trades.csv"
        if not csv_path.exists():
            print(f"Quik-лента не найдена: {csv_path}")
        else:
            need = set(csv_needed)
            dt0 = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=MSK)
            start_ms = int(dt0.timestamp() * 1000)
            end_ms = start_ms + 86_400_000
            kept = 0
            lines = 0
            with open(csv_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    lines += 1
                    if lines % 200000 == 0:
                        print(f"  чтение ленты: {lines} строк, сохранено {kept} сделок", flush=True)
                    p = line.strip().split(";")
                    if len(p) < 5:
                        continue
                    sym = p[0]
                    if sym not in need:
                        continue
                    try:
                        ts = int(float(p[4]))
                    except ValueError:
                        continue
                    if ts < start_ms or ts >= end_ms:
                        continue
                    try:
                        trades_by_sym.setdefault(sym, []).append({
                            "symbol": sym, "qty": int(float(p[1])),
                            "price": float(p[2]), "side": p[3], "timestamp": ts})
                        kept += 1
                    except ValueError:
                        continue
            print(f"  чтение ленты: {lines} строк, сохранено {kept} сделок", flush=True)

    # 3) детекторы по каждому тикеру
    rows = []
    for i, symbol in enumerate(needed, 1):
        print(f"\r[{i}/{len(needed)}] {symbol}      ", end="", flush=True)
        trades = trades_by_sym.get(symbol)
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
    print()  # перевод строки после прогресса

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