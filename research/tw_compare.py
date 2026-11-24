"""
tw_compare.py — сравнение нашей истории с эталоном T-Widgets.
FP считаем ТОЛЬКО по тикерам, которые TW мониторит (иначе штрафуем себя
за реальные находки вне области видимости эталона). Детекции вне скоупа
выводим отдельно. Показываем задержку детекции (мс).
"""
import sys
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
MSK = ZoneInfo("Europe/Moscow")

def load_jsonl_safe(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.loads(fh.read())
            if isinstance(data, dict) and "payload" in data and "robots" in data["payload"]:
                return data["payload"]["robots"]
            if isinstance(data, list):
                return data
            return [data]
    except json.JSONDecodeError:
        pass
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out

def build_tw_entities(events):
    ents = []
    for e in events:
        try:
            ls = datetime.fromisoformat(e.get("start", "").replace("Z", "+00:00")).timestamp()
            le = datetime.fromisoformat(e.get("end", "").replace("Z", "+00:00")).timestamp()
        except (KeyError, ValueError):
            continue
        ents.append({
            "id": e.get("id"), "ticker": e.get("ticker"),
            "side": "BUY" if e.get("isBuy") else "SELL",
            "period": float(e.get("interval", 0)) / 1000.0,
            "lo": float(e.get("minLots", 0)), "hi": float(e.get("maxLots", 0)),
            "op": int(e.get("count", 0)), "ls": ls, "le": le,
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

def overlap_score(a, b, c, d):
    os_, oe = max(a, c), min(b, d)
    if os_ < oe:
        return oe - os_
    if b < c:
        return -(c - b)
    return -(a - d)

def main():
    date = sys.argv[1] if len(sys.argv) > 1 else "2026-11-13"
    ws, we = "10:00", "16:16"
    if "--ws" in sys.argv: ws = sys.argv[sys.argv.index("--ws") + 1]
    if "--we" in sys.argv: we = sys.argv[sys.argv.index("--we") + 1]
    ours_path = BASE / "data" / "robots_history.jsonl"
    if "--ours" in sys.argv:
        ours_path = Path(sys.argv[sys.argv.index("--ours") + 1])
    tw_path = BASE / "data" / f"tw_robots_{date}.jsonl"
    if not tw_path.exists() or not ours_path.exists():
        print("[!] нет файла эталона или истории"); return

    print(f"[tw_compare] эталон: {tw_path.name} | наша история: {ours_path}")
    tw_ents = build_tw_entities(load_jsonl_safe(tw_path))
    universe = {e["ticker"] for e in tw_ents}
    bursts = [e for e in tw_ents if e["period"] < 2.0 or (e["le"] - e["ls"]) < 4.0 or e["period"] > 60.0]
    real_tw = [e for e in tw_ents if e not in bursts]
    print(f"[tw_compare] TW всего={len(tw_ents)} bursts/long={len(bursts)} реальных={len(real_tw)}")
    print(f"[tw_compare] область TW (тикеры): {sorted(universe)}")

    d0 = datetime.strptime(date, "%Y-%m-%d").date()
    w0 = datetime.combine(d0, datetime.strptime(ws, "%H:%M").time(), tzinfo=MSK).timestamp()
    w1 = datetime.combine(d0, datetime.strptime(we, "%H:%M").time(), tzinfo=MSK).timestamp()
    ours = [o for o in load_jsonl_safe(ours_path)
            if 0 < float(o.get("interval_avg") or 0) <= 60.0
            and w0 <= (our_window(o)[0] or -1) <= w1]
    print(f"[tw_compare] наших серий в окне: {len(ours)}")

    by_key = defaultdict(list)
    for e in real_tw:
        by_key[(e["ticker"], e["side"])].append(e)

    admissible = [False] * len(ours)
    pairs = []
    for oi, o in enumerate(ours):
        st, en = our_window(o)
        iv = float(o["interval_avg"]); qset = set(o.get("qty_variants", []))
        if not qset: continue
        for e in by_key.get((o["symbol"], o["side"].upper()), []):
            if abs(iv - e["period"]) > max(2.0, e["period"] * 0.2): continue
            if not any(e["lo"] <= q <= e["hi"] for q in qset): continue
            admissible[oi] = True
            pairs.append((overlap_score(st, en, e["ls"], e["le"]), oi, e["id"]))
    pairs.sort(key=lambda x: -x[0])
    matched, matched_ours, tp_list = set(), set(), []
    for score, oi, eid in pairs:
        if eid in matched or oi in matched_ours: continue
        matched.add(eid); matched_ours.add(oi)
        te = next(e for e in real_tw if e["id"] == eid)
        rec = ours[oi]
        oms = rec.get("start_ms") or (rec.get("start_ts") or 0) * 1000
        tp_list.append((rec, te, oms - te["ls"] * 1000))

    fp_all = [o for i, o in enumerate(ours) if not admissible[i]]
    fp_scope = [o for o in fp_all if o["symbol"] in universe]
    fp_out = [o for o in fp_all if o["symbol"] not in universe]
    fn_list = [e for e in real_tw if e["id"] not in matched]
    tp, fp, fn = len(tp_list), len(fp_scope), len(fn_list)

    print("\n" + "=" * 70)
    print(f"TP={tp}  FP(в скоупе TW)={fp}  FN={fn}  | вне скоупа TW={len(fp_out)} (не FP)")
    if tp + fp: print(f"Precision(честная)={100.0*tp/(tp+fp):.1f}%")
    if tp + fn: print(f"Recall={100.0*tp/(tp+fn):.1f}%")
    print(f"METRICS: TP={tp} FP={fp} FN={fn} OUT={len(fp_out)}")

    print("\n--- TP (задержка детекции) ---")
    for rec, te, delay in tp_list[:30]:
        print(f"  {rec['symbol']:6s} {rec['side']:4s} наш={rec['interval_avg']:.1f}s tw={te['period']:.1f}s | задержка {delay:+.0f} мс")
    print("\n--- FP в скоупе TW (первые 20) ---")
    for o in fp_scope[:20]:
        st, _ = our_window(o)
        print(f"  {o['symbol']:6s} {o['side']:4s} int={float(o['interval_avg']):.1f}s qty={o.get('qty_variants')} повт={o['repeats']}")
    print("\n--- FN ---")
    for e in sorted(fn_list, key=lambda x: -x["period"])[:20]:
        print(f"  {e['ticker']:6s} {e['side']:4s} int={e['period']:.1f}s qty=[{e['lo']:.0f},{e['hi']:.0f}] count={e['op']}")
    print("\n[tw_compare] Готово.")

if __name__ == "__main__":
    main()