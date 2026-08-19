"""
Приблуда на python — снапшот роботов на момент времени.
Режимы:
  обычный: python analysis/robot_snapshot.py 2026-08-17 11:34
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
from config import get_detector_configs, get_min_qty_percentile
from detectors.interval_robot import IntervalRobotDetector
from stats import qty_percentile
from ticker_settings import get_active_symbols, load_settings

DATA = BASE / "data"
OUT = BASE / "output"
MIN_REP = 5

TARGETS = {
    ("buy", "RUAL"), ("buy", "BSPB"), ("buy", "OZON"),
    ("sell", "X5"), ("sell", "MDNG"), ("sell", "SNGSP"),
    ("sell", "TRNFP"), ("sell", "LKOH"), ("sell", "SBER"),
}


def main():
    if len(sys.argv) < 3:
        print("Использование: python analysis/robot_snapshot.py 2026-08-17 11:34 [loose]")
        return
    date, hm = sys.argv[1], sys.argv[2]
    loose = len(sys.argv) > 3 and sys.argv[3] == "loose"
    T = datetime.strptime(f"{date} {hm}", "%Y-%m-%d %H:%M").timestamp()
    settings = load_settings()

    rows = []
    for symbol in get_active_symbols(settings):
        if loose and not any(t[1] == symbol for t in TARGETS):
            continue
        path = DATA / f"{symbol}_{date}.json"
        if not path.exists():
            continue
        trades = json.load(open(path, encoding="utf-8"))
        override = settings.get(symbol, {})
        manual = override.get("min_qty")
        min_qty = manual if manual is not None else qty_percentile(trades, get_min_qty_percentile(symbol))
        dets = [IntervalRobotDetector(symbol, c) for c in get_detector_configs(symbol, min_qty, override)]
        if loose:
            for d in dets:
                d.min_qty = 1
                d.CLOSE_AFTER_MISSES = 5
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