"""
fp_audit.py — классификация FP по ЛЕНТЕ (а не по эталону).
FP = наши серии без параметрически подходящего эталона Aniscan.
Для каждой смотрим окно в tape: сетка есть -> REAL-GRID (робот, который
Aniscan не зарегистрировал), сетки нет -> NOISE.
Отвечает на вопрос: врём мы (noise) или недооценены (дыра эталона).
"""
import sys
from collections import defaultdict
from datetime import datetime
from day_audit import (load_tape, build_entities, load_jsonl, our_window,
                       BASE, MSK)


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else "2026-11-13"
    ws, we = "10:00", "16:16"
    if "--ws" in sys.argv:
        ws = sys.argv[sys.argv.index("--ws") + 1]
    if "--we" in sys.argv:
        we = sys.argv[sys.argv.index("--we") + 1]
    d0 = datetime.strptime(date, "%Y-%m-%d").date()
    w0 = datetime.combine(d0, datetime.strptime(ws, "%H:%M").time(), tzinfo=MSK).timestamp()
    w1 = datetime.combine(d0, datetime.strptime(we, "%H:%M").time(), tzinfo=MSK).timestamp()

    idx, syms, ntr = load_tape(BASE / "data" / f"live_trades_{date}.jsonl")
    ents = [e for e in build_entities(load_jsonl(BASE / "data" / f"aniscan_robots_{date}.jsonl"))
            if e["itype"] == "STOCK" and e["ticker"] in syms
            and 0 < e["period"] <= 60.0 and w0 <= e["ls"] <= w1]
    ours = [o for o in load_jsonl(BASE / "data" / "replay_alor_history.jsonl")
            if 0 < float(o.get("interval_avg") or 0) <= 60.0
            and w0 <= (our_window(o)[0] or -1) <= w1]
    by_key = defaultdict(list)
    for e in ents:
        by_key[(e["ticker"], e["side"])].append(e)

    fp = []
    for o in ours:
        st, en = our_window(o)
        iv = float(o["interval_avg"])
        qset = set(o.get("qty_variants", []))
        ok = False
        for e in by_key.get((o["symbol"], o["side"].upper()), []):
            if abs(iv - e["period"]) <= max(2.0, e["period"] * 0.2) and qset \
               and all(e["lo"] <= q <= e["hi"] for q in qset):
                ok = True
                break
        if not ok:
            fp.append(o)

    cls = defaultdict(list)
    for o in fp:
        st, en = our_window(o)
        iv = float(o["interval_avg"])
        qset = set(o.get("qty_variants", []))
        arr = idx.get((o["symbol"], o["side"].lower()), [])
        hits = [(t, q) for t, q in arr if st - 5 <= t <= en + 5 and q in qset]
        n = len(hits)
        if n < 8:
            cls["THIN"].append((o, n, 0.0))
            continue
        gaps = [hits[i+1][0] - hits[i][0] for i in range(n - 1)]
        gs = sorted(gaps)
        med = gs[len(gs) // 2]
        reg = sum(1 for g in gaps if abs(g - med) <= max(2.0, 0.2 * med)) / len(gaps)
        if reg >= 0.6 and abs(med - iv) <= max(2.0, 0.2 * iv):
            cls["REAL-GRID"].append((o, n, reg))
        else:
            cls["NOISE"].append((o, n, reg))

    print("\n" + "=" * 68)
    print(f" FP-АУДИТ ПО ЛЕНТЕ ({date}, окно {ws}-{we}): всего FP={len(fp)}")
    print("=" * 68)
    for c in ("REAL-GRID", "NOISE", "THIN"):
        print(f"   {c:10s}: {len(cls[c])}")
    print("\n REAL-GRID (роботы, которые Aniscan НЕ зарегистрировал), топ-20:")
    rg = sorted(cls["REAL-GRID"], key=lambda x: -x[1])
    for o, n, reg in rg[:20]:
        st, _ = our_window(o)
        t0 = datetime.fromtimestamp(st, MSK).strftime("%H:%M:%S")
        print(f"   {o['symbol']:6s} {o['side']:4s} int={float(o['interval_avg']):5.1f}s "
              f"reps={o['repeats']:4d} qty={o.get('qty_variants')} старт={t0} "
              f"ударов={n} regular={reg:.0%}")
    print("\n NOISE (наш мусор), топ-10:")
    for o, n, reg in sorted(cls["NOISE"], key=lambda x: -x[1])[:10]:
        st, _ = our_window(o)
        t0 = datetime.fromtimestamp(st, MSK).strftime("%H:%M:%S")
        print(f"   {o['symbol']:6s} {o['side']:4s} int={float(o['interval_avg']):5.1f}s "
              f"reps={o['repeats']:4d} qty={o.get('qty_variants')} старт={t0} "
              f"ударов={n} regular={reg:.0%}")
    print("=" * 68)


if __name__ == "__main__":
    main()