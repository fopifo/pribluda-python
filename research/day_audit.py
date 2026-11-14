"""
day_audit.py — вечерний аудит дня.
Метрики v6: глобальный жадный матчинг (qty+interval+time-tiebreak),
FP = только серии БЕЗ параметрически подходящего эталона; повторные
детекции того же робота (эпизоды) = re-detections, не FP.
Классы FN + оконный дамп GRID-IN-TAPE + аудит FP.
"""
import sys
import json
import bisect
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
MSK = ZoneInfo("Europe/Moscow")
GRACE = 60.0


def bar(pct, extra):
    filled = int(max(0.0, min(100.0, pct)) // 5)
    sys.stdout.write(f"\r[лента] [{'#'*filled}{'.'*(20-filled)}] {pct:3.0f}% | {extra}")
    sys.stdout.flush()


def load_tape(path):
    idx = defaultdict(list)
    syms = set()
    size = path.stat().st_size
    done = 0
    with open(path, "rb") as fh:
        for i, raw in enumerate(fh):
            if i % 20000 == 0:
                bar(100.0 * fh.tell() / size, f"{fh.tell()//1024}KB/{size//1024}KB")
            try:
                d = json.loads(raw)
            except json.JSONDecodeError:
                continue
            sym = d.get("symbol")
            side = (d.get("side") or "").lower()
            ts = d.get("timestamp")
            if not sym or not side or ts is None:
                continue
            idx[(sym, side)].append((ts / 1000.0, float(d.get("qty") or 0)))
            syms.add(sym)
            done += 1
    sys.stdout.write("\n")
    for k in idx:
        idx[k].sort(key=lambda x: x[0])
    return idx, syms, done


def load_jsonl(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def build_entities(events):
    by_id = defaultdict(list)
    for e in events:
        rid = (e.get("robot") or {}).get("id")
        if rid:
            by_id[rid].append(e)
    ents = []
    for rid, evs in by_id.items():
        ts = []
        for e in evs:
            try:
                ts.append(datetime.fromisoformat(e["createDttm"]).timestamp())
            except (KeyError, ValueError):
                continue
        if not ts:
            continue
        f0 = evs[0]
        ents.append({
            "id": rid,
            "ticker": f0.get("ticker"),
            "side": (f0.get("operationType") or "").upper(),
            "period": float(f0.get("period") or 0),
            "lo": float(f0.get("minLot") or 0),
            "hi": float(f0.get("maxLot") or 0),
            "itype": f0.get("instrumentType"),
            "op": max(int(e.get("operationCount") or 0) for e in evs),
            "ls": min(ts),
            "le": max(ts),
        })
    return ents


def our_window(rec):
    st = rec.get("start_ts") or (rec.get("start_ms") or 0) / 1000.0 or None
    if st is None:
        return None, None
    en = rec.get("end_ts") or rec.get("last_ts")
    if en is None:
        en = st + float(rec.get("repeats") or 1) * float(rec.get("interval_avg") or 0)
    return float(st), float(en)


def classify(ent, idx):
    key = (ent["ticker"], ent["side"].lower())
    arr = idx.get(key)
    if not arr:
        return "NO-TRADES", 0, 0.0
    a = ent["ls"] - 30.0
    b = ent["le"] + 30.0
    i0 = bisect.bisect_left(arr, (a, -1.0))
    i1 = bisect.bisect_right(arr, (b, 1e18))
    hits = [(t, q) for t, q in arr[i0:i1] if ent["lo"] <= q <= ent["hi"]] if ent["hi"] else []
    n = len(hits)
    if n < 5:
        return ("THIN" if n else "NO-TRADES"), n, 0.0
    gaps = [hits[i+1][0] - hits[i][0] for i in range(n - 1)]
    gs = sorted(gaps)
    med = gs[len(gs) // 2]
    tol = max(2.0, 0.25 * med)
    reg = sum(1 for g in gaps if abs(g - med) <= tol) / len(gaps)
    if reg >= 0.6 and abs(med - ent["period"]) <= max(2.0, 0.25 * ent["period"]):
        return "GRID-IN-TAPE", n, med
    if n >= 10 and med <= ent["period"] / 2.5:
        return "BURST-AGG", n, med
    return "IRREGULAR", n, med


def window_overlap_score(our_st, our_en, ref_ls, ref_le):
    os_ = max(our_st, ref_ls)
    oe = min(our_en, ref_le)
    if os_ < oe:
        return oe - os_
    if our_en < ref_ls:
        return -(ref_ls - our_en)
    return -(our_st - ref_le)


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else "2026-11-10"
    ws, we = "10:00", "16:16"
    if "--ws" in sys.argv:
        ws = sys.argv[sys.argv.index("--ws") + 1]
    if "--we" in sys.argv:
        we = sys.argv[sys.argv.index("--we") + 1]
    d0 = datetime.strptime(date, "%Y-%m-%d").date()
    w0 = datetime.combine(d0, datetime.strptime(ws, "%H:%M").time(), tzinfo=MSK).timestamp()
    w1 = datetime.combine(d0, datetime.strptime(we, "%H:%M").time(), tzinfo=MSK).timestamp()

    tape_path = BASE / "data" / f"live_trades_{date}.jsonl"
    hist_path = BASE / "data" / "replay_alor_history.jsonl"
    ani_path = BASE / "data" / f"aniscan_robots_{date}.jsonl"
    for p in (tape_path, hist_path, ani_path):
        if not p.exists():
            print(f"[!] нет файла {p.name}"); return

    idx, syms, ntr = load_tape(tape_path)
    print(f"[*] лента: {ntr} сделок, {len(syms)} тикеров; окно {ws}-{we}")

    ents = [e for e in build_entities(load_jsonl(ani_path))
            if e["itype"] == "STOCK" and e["ticker"] in syms
            and 0 < e["period"] <= 60.0 and w0 <= e["ls"] <= w1]
    ours = [o for o in load_jsonl(hist_path)
            if 0 < float(o.get("interval_avg") or 0) <= 60.0
            and w0 <= (our_window(o)[0] or -1) <= w1]
    print(f"[*] эталон в скоупе+окне: {len(ents)}; наших серий в окне: {len(ours)}")

    by_key = defaultdict(list)
    for e in ents:
        by_key[(e["ticker"], e["side"])].append(e)

    # v6: admissible = есть ли хоть один параметрически подходящий эталон
    admissible = [False] * len(ours)
    pairs = []
    for oi, o in enumerate(ours):
        our_st, our_en = our_window(o)
        our_iv = float(o["interval_avg"])
        our_qty = set(o.get("qty_variants", []))
        if not our_qty:
            continue
        for e in by_key.get((o["symbol"], o["side"].upper()), []):
            if abs(our_iv - e["period"]) > max(2.0, e["period"] * 0.2):
                continue
            if not all(e["lo"] <= q <= e["hi"] for q in our_qty):
                continue
            admissible[oi] = True
            pairs.append((window_overlap_score(our_st, our_en, e["ls"], e["le"]), oi, e["id"]))
    pairs.sort(key=lambda x: -x[0])
    matched = set()
    matched_ours = set()
    tp_overlap = 0
    tp_param_only = 0
    for score, oi, eid in pairs:
        if eid in matched or oi in matched_ours:
            continue
        matched.add(eid)
        matched_ours.add(oi)
        if score > 0:
            tp_overlap += 1
        else:
            tp_param_only += 1
    tp = len(matched)
    fp_list = [o for i, o in enumerate(ours) if not admissible[i]]
    redetect = sum(1 for i in range(len(ours)) if admissible[i] and i not in matched_ours)
    fp = len(fp_list)
    fn_list = [e for e in ents if e["id"] not in matched]
    fn = len(fn_list)

    print("\n" + "=" * 68)
    print(f" МЕТРИКИ v6 (окно {ws}-{we}; FP=без подходящего эталона)")
    print("=" * 68)
    print(f" TP={tp} (overlap={tp_overlap}, param-only={tp_param_only})  FP={fp}  FN={fn}  re-detect={redetect}")
    if tp + fp:
        print(f" Precision {100.0*tp/(tp+fp):.2f}% ({tp}/{tp+fp})")
    if tp + fn:
        print(f" Recall    {100.0*tp/(tp+fn):.2f}% ({tp}/{tp+fn})")

    print("\n" + "-" * 68)
    print(" КЛАССЫ FN (сетка в ленте?):")
    cls = defaultdict(list)
    for e in fn_list:
        c, n, med = classify(e, idx)
        cls[c].append((e, n, med))
    for c in ("GRID-IN-TAPE", "BURST-AGG", "IRREGULAR", "THIN", "NO-TRADES"):
        print(f"   {c:13s}: {len(cls[c])}")
    print("\n GRID-IN-TAPE (наш баг, топ-15):")
    for e, n, med in cls["GRID-IN-TAPE"][:15]:
        t0 = datetime.fromtimestamp(e["ls"], MSK).strftime("%H:%M:%S")
        print(f"   {e['ticker']:6s} {e['side']:4s} int={e['period']:.0f}s "
              f"lots={e['lo']:.0f}-{e['hi']:.0f} старт={t0} ударов={n} med={med:.1f}s")

    long_fp = [o for o in fp_list if float(o["interval_avg"]) > 120.0]
    short_fp = [o for o in fp_list if float(o["interval_avg"]) <= 120.0]
    print("\n" + "-" * 68)
    print(f" АУДИТ FP: всего {fp} (короткие <=120s: {len(short_fp)}, "
          f"длинные >120s: {len(long_fp)})")
    long_fp.sort(key=lambda o: -int(o.get("repeats") or 0))
    for o in long_fp[:10]:
        st, _ = our_window(o)
        t0 = datetime.fromtimestamp(st, MSK).strftime("%H:%M:%S")
        print(f"   {o['symbol']:6s} {o['side']:4s} int={float(o['interval_avg']):.0f}s "
              f"reps={o['repeats']} старт={t0}")

    print("\n" + "-" * 68)
    print(" ОКОННЫЙ ДАМП GRID-IN-TAPE (топ-5):")
    for e, n, med in cls["GRID-IN-TAPE"][:5]:
        sym = e["ticker"]
        side = e["side"].lower()
        arr = idx.get((sym, side), [])
        if not arr:
            print(f"\n   {sym} {e['side']}: сделок нет")
            continue
        a = e["ls"] - 30.0
        b = e["le"] + 30.0
        window = [(t, q) for t, q in arr if a <= t <= b and e["lo"] <= q <= e["hi"]]
        print(f"\n   {sym} {e['side']} int={e['period']:.0f}s lots={e['lo']:.0f}-{e['hi']:.0f} "
              f"старт={datetime.fromtimestamp(e['ls'], MSK).strftime('%H:%M:%S')} "
              f"ударов_ожид={n} найдено={len(window)}")
        if len(window) < 20:
            for i, (t, q) in enumerate(window):
                tm = datetime.fromtimestamp(t, MSK).strftime("%H:%M:%S.%f")[:-3]
                gap = (t - window[i-1][0]) if i > 0 else 0.0
                print(f"     {i:2d} {tm} qty={q:6.0f} gap={gap:6.1f}s")
        else:
            for i, (t, q) in enumerate(window[:10]):
                tm = datetime.fromtimestamp(t, MSK).strftime("%H:%M:%S.%f")[:-3]
                gap = (t - window[i-1][0]) if i > 0 else 0.0
                print(f"     {i:2d} {tm} qty={q:6.0f} gap={gap:6.1f}s")
            print(f"     ... ({len(window) - 20} пропущено) ...")
            for i, (t, q) in enumerate(window[-10:], len(window) - 10):
                tm = datetime.fromtimestamp(t, MSK).strftime("%H:%M:%S.%f")[:-3]
                gap = (t - window[i-1][0]) if i > 0 else 0.0
                print(f"     {i:2d} {tm} qty={q:6.0f} gap={gap:6.1f}s")
        if len(window) >= 2:
            gaps = [window[i][0] - window[i-1][0] for i in range(1, len(window))]
            gs = sorted(gaps)
            med2 = gs[len(gs) // 2]
            reg = sum(1 for g in gaps if abs(g - med2) <= max(2.0, 0.2 * med2)) / len(gaps)
            print(f"     gaps: min={min(gaps):.1f}s max={max(gaps):.1f}s "
                  f"median={med2:.1f}s regular={reg:.0%}")
            if reg >= 0.6 and abs(med2 - e["period"]) <= max(2.0, 0.2 * e["period"]):
                print("     => СЕТКА ЕСТЬ => детектор должен был поймать => БАГ")
            else:
                print("     => НЕ сетка (IRREGULAR), детектор прав что не ловит")
    print("=" * 68)


if __name__ == "__main__":
    main()