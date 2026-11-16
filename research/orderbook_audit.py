"""
orderbook_audit.py v4 — детектор периодических слоёв по стакану.
Режимы: человек (по умолчанию) и --json (для бота, без прогресс-баров).
Фильтры: --min-n, --min-reg, --flash-only; пороги: --thr, --p90, --min-lots, --sweep.
"""
import sys
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
MSK = ZoneInfo("Europe/Moscow")
QUIET = False


def bar(pct, extra):
    if QUIET:
        return
    filled = int(max(0.0, min(100.0, pct)) // 5)
    sys.stdout.write(f"\r[стакан] [{'#'*filled}{'.'*(20-filled)}] {pct:3.0f}% | {extra}")
    sys.stdout.flush()


def median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def load(path):
    series = defaultdict(list)
    amounts = defaultdict(list)
    ts_range = defaultdict(lambda: [None, None])
    count = defaultdict(int)
    size = path.stat().st_size
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
            count[sym] += 1
            r = ts_range[sym]
            r[0] = ts if r[0] is None else min(r[0], ts)
            r[1] = ts if r[1] is None else max(r[1], ts)
            for side in ("bids", "asks"):
                for row in d.get(side, []):
                    if len(row) != 2:
                        continue
                    price, amt = row
                    series[(sym, side, price)].append((ts, amt))
                    amounts[sym].append(amt)
    if not QUIET:
        sys.stdout.write("\n")
    return series, amounts, count, ts_range


def classify(med, life):
    if life is None:
        return "?"
    if life < 0.5 * med:
        return "FLASH"
    if life > 0.8 * med:
        return "PULL"
    return "MID"


def detect(series, thr_map, min_n=4, min_reg=0.6, flash_only=False):
    out = []
    for (sym, side, price), pts in series.items():
        if len(pts) < 8:
            continue
        t = thr_map.get(sym, 200)
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
        if len(adds) < min_n:
            continue
        gaps = [(adds[i + 1] - adds[i]) / 1000.0 for i in range(len(adds) - 1)]
        med = median(gaps)
        if not (3.0 <= med <= 180.0):
            continue
        tol = max(1.0, 0.25 * med)
        reg = sum(1 for g in gaps if abs(g - med) <= tol) / len(gaps)
        if reg < min_reg:
            continue
        life = sum(lifetimes) / len(lifetimes) if lifetimes else None
        tag = classify(med, life)
        if flash_only and tag != "FLASH":
            continue
        out.append({"sym": sym, "side": side, "price": price, "period_s": med,
                    "n": len(adds), "reg": reg, "life_s": life, "tag": tag})
    out.sort(key=lambda c: -c["n"])
    return out


def main():
    global QUIET
    a = sys.argv
    if "--json" in a:
        QUIET = True
    date = a[1] if len(a) > 1 and not a[1].startswith("--") else datetime.now(MSK).strftime("%Y-%m-%d")
    min_lots, p90f, thr_abs, sweep = 200.0, 0.4, None, None
    min_n, min_reg, flash_only = 4, 0.6, False
    if "--min-lots" in a: min_lots = float(a[a.index("--min-lots") + 1])
    if "--p90" in a: p90f = float(a[a.index("--p90") + 1])
    if "--thr" in a: thr_abs = float(a[a.index("--thr") + 1])
    if "--min-n" in a: min_n = int(a[a.index("--min-n") + 1])
    if "--min-reg" in a: min_reg = float(a[a.index("--min-reg") + 1])
    if "--flash-only" in a: flash_only = True
    if "--sweep" in a:
        sweep = [100, 200, 500, 1000, 2000, 5000, 10000]
        nxt = a[a.index("--sweep") + 1:a.index("--sweep") + 2]
        if nxt and not nxt[0].startswith("--"):
            sweep = [float(x) for x in nxt[0].split(",")]
    path = BASE / "data" / f"orderbook_{date}.jsonl"
    if not path.exists():
        if QUIET:
            print(json.dumps({"error": "no file", "candidates": []}))
        else:
            print(f"[!] нет файла {path.name}")
        return

    series, amounts, count, ts_range = load(path)
    activity = {}
    for sym in sorted(count):
        r = ts_range[sym]
        span = (r[1] - r[0]) / 1000.0 if r[0] is not None else 0
        activity[sym] = {"snapshots": count[sym], "span_s": round(span, 1),
                         "rate": round(count[sym] / span, 2) if span > 0 else 0}

    thr_default = {}
    for sym, amts in amounts.items():
        s = sorted(amts)
        p90 = s[int(0.90 * (len(s) - 1))] if s else 0
        thr_default[sym] = max(min_lots, p90f * p90)

    if sweep and not QUIET:
        print(f"[*] уровней: {len(series)}")
        for sym in sorted(activity):
            print(f"    {sym:10s} снапшотов={activity[sym]['snapshots']:6d}  окно={activity[sym]['span_s']:8.1f}s  темп={activity[sym]['rate']:4.2f}/s")
        print(f"\n[*] SWEEP (min-n={min_n}, min-reg={min_reg:.0%}):")
        for t in sweep:
            tm = {sym: t for sym in amounts}
            c = detect(series, tm, min_n, min_reg, flash_only)
            top = "; ".join(f"{x['sym']} {x['side']} {x['price']} P={x['period_s']:.0f}s n={x['n']} {x['tag']}" for x in c[:3])
            print(f"    thr={t:7.0f} -> {len(c):3d}  {top}")
        return

    thr_map = {sym: (thr_abs if thr_abs is not None else thr_default[sym]) for sym in amounts}
    cands = detect(series, thr_map, min_n, min_reg, flash_only)

    if QUIET:
        print(json.dumps({"activity": activity, "candidates": cands}, ensure_ascii=False))
        return

    print(f"[*] уровней: {len(series)}")
    for sym in sorted(activity):
        print(f"    {sym:10s} снапшотов={activity[sym]['snapshots']:6d}  окно={activity[sym]['span_s']:8.1f}s  темп={activity[sym]['rate']:4.2f}/s")
    for sym in sorted(thr_map):
        print(f"    {sym:10s} порог={thr_map[sym]:.0f}")
    print("\n" + "=" * 80)
    print(f" ПЕРИОДИЧЕСКИЕ СЛОИ: {len(cands)}  (min-n={min_n}, min-reg={min_reg:.0%})")
    print("=" * 80)
    for c in cands[:25]:
        ls = f"{c['life_s']:.1f}s" if c['life_s'] is not None else "n/a"
        print(f"  {c['sym']:10s} {c['side']:4s} price={c['price']:<10} P={c['period_s']:6.1f}s n={c['n']:3d} reg={c['reg']:.0%} жизнь={ls:7s} {c['tag']}")
    if not cands:
        print("  (нет надёжных кандидатов при этих фильтрах)")
    print("=" * 80)


if __name__ == "__main__":
    main()