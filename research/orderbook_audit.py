"""
orderbook_audit.py — детектор периодических слоёв (спуфинг) по стакану.
Ищет уровни цены, где крупная заявка регулярно появляется/снимается.
Читает data/orderbook_DATE.jsonl (пишет PbSdk мост).
"""
import sys
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
MSK = ZoneInfo("Europe/Moscow")


def bar(pct, extra):
    filled = int(max(0.0, min(100.0, pct)) // 5)
    sys.stdout.write(f"\r[стакан] [{'#'*filled}{'.'*(20-filled)}] {pct:3.0f}% | {extra}")
    sys.stdout.flush()


def median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def load(path):
    series = defaultdict(list)   # (sym, side, price) -> [(ts, amt)]
    amounts = defaultdict(list)  # sym -> [amt]
    size = path.stat().st_size
    done = 0
    with open(path, "rb") as fh:
        for i, raw in enumerate(fh):
            if i % 5000 == 0:
                bar(100.0 * fh.tell() / size, f"{fh.tell()//1024}KB/{size//1024}KB")
            try:
                d = json.loads(raw)
            except json.JSONDecodeError:
                continue
            sym = d.get("symbol")
            ts = d.get("timestamp")
            if not sym or ts is None:
                continue
            for side in ("bids", "asks"):
                for row in d.get(side, []):
                    if len(row) != 2:
                        continue
                    price, amt = row
                    series[(sym, side, price)].append((ts, amt))
                    amounts[sym].append(amt)
            done += 1
    sys.stdout.write("\n")
    return series, amounts, done


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.now(MSK).strftime("%Y-%m-%d")
    min_lots = 200
    if "--min-lots" in sys.argv:
        min_lots = float(sys.argv[sys.argv.index("--min-lots") + 1])
    path = BASE / "data" / f"orderbook_{date}.jsonl"
    if not path.exists():
        print(f"[!] нет файла {path.name}"); return

    series, amounts, n = load(path)
    print(f"[*] снапшотов: {n}; уровней: {len(series)}")

    thr = {}
    for sym, amts in amounts.items():
        s = sorted(amts)
        p90 = s[int(0.90 * (len(s) - 1))] if s else 0
        thr[sym] = max(min_lots, 0.4 * p90)
        print(f"    {sym:8s} порог слоя = {thr[sym]:.0f} лотов")

    candidates = []
    keys = list(series.keys())
    for ki, key in enumerate(keys):
        if ki % 200 == 0:
            bar(100.0 * ki / len(keys), f"{ki}/{len(keys)}")
        sym, side, price = key
        pts = series[key]
        if len(pts) < 8:
            continue
        t = thr[sym]
        adds = []
        lifetimes = []
        last_add = None
        prev = False
        for ts, amt in pts:
            state = amt >= t
            if state and not prev:
                last_add = ts
                adds.append(ts)
            elif not state and prev:
                if last_add is not None:
                    lifetimes.append((ts - last_add) / 1000.0)
                last_add = None
            prev = state
        if len(adds) < 4:
            continue
        gaps = [(adds[i + 1] - adds[i]) / 1000.0 for i in range(len(adds) - 1)]
        med = median(gaps)
        if not (3.0 <= med <= 180.0):
            continue
        tol = max(1.0, 0.25 * med)
        reg = sum(1 for g in gaps if abs(g - med) <= tol) / len(gaps)
        if reg < 0.6:
            continue
        mean_life = sum(lifetimes) / len(lifetimes) if lifetimes else None
        flash = mean_life is not None and mean_life < med * 0.5
        candidates.append((sym, side, price, med, len(adds), reg, mean_life, flash))
    sys.stdout.write("\n")

    candidates.sort(key=lambda c: -c[4])
    print("\n" + "=" * 78)
    print(f" ПЕРИОДИЧЕСКИЕ СЛОИ (спуфинг-кандидаты): {len(candidates)}")
    print("=" * 78)
    for sym, side, price, med, cnt, reg, life, flash in candidates[:25]:
        tag = "FLASH(быстро снимает)" if flash else ""
        life_s = f"{life:.1f}s" if life is not None else "n/a"
        print(f"  {sym:8s} {side:4s} price={price:<10} период={med:6.1f}s "
              f"появл={cnt:3d} reg={reg:.0%} жизнь={life_s:7s} {tag}")
    if not candidates:
        print("  (пока нет регулярных слоёв — рынок спокоен или порог высок)")
    print("=" * 78)


if __name__ == "__main__":
    main()