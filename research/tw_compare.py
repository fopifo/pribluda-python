"""
tw_compare.py — сравнение нашей истории детекций с эталоном T-Widgets.
Считает TP, FP, FN и задержку детекции (в мс).
"""
import sys
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
MSK = ZoneInfo("Europe/Moscow")

def load_tw_robots(path):
    """Загружает файл TW-роботов, учитывая вложенную структуру payload['robots']."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.loads(fh.read())
            if isinstance(data, dict) and "payload" in data and "robots" in data["payload"]:
                return data["payload"]["robots"]
            elif isinstance(data, list):
                return data
            else:
                return [data]
    except json.JSONDecodeError:
        pass
    
    # Fallback: построчное чтение (NDJSON)
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
            # TW отдаёт время в ISO формате с 'Z' на конце
            start_str = e.get("start", "").replace("Z", "+00:00")
            end_str = e.get("end", "").replace("Z", "+00:00")
            ls = datetime.fromisoformat(start_str).timestamp()
            le = datetime.fromisoformat(end_str).timestamp()
        except (KeyError, ValueError):
            continue
        
        ents.append({
            "id": e.get("id"),
            "ticker": e.get("ticker"),
            "side": "BUY" if e.get("isBuy") else "SELL",
            "period": float(e.get("interval", 0)) / 1000.0, # TW отдаёт интервал в мс, конвертируем в секунды
            "lo": float(e.get("minLots", 0)),
            "hi": float(e.get("maxLots", 0)),
            "op": int(e.get("count", 0)),
            "ls": ls,
            "le": le,
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

def window_overlap_score(our_st, our_en, ref_ls, ref_le):
    os_ = max(our_st, ref_ls)
    oe = min(our_en, ref_le)
    if os_ < oe:
        return oe - os_
    if our_en < ref_ls:
        return -(ref_ls - our_en)
    return -(our_st - ref_le)

def main():
    date = sys.argv[1] if len(sys.argv) > 1 else "2026-11-13"
    ws, we = "10:00", "16:16"
    if "--ws" in sys.argv:
        ws = sys.argv[sys.argv.index("--ws") + 1]
    if "--we" in sys.argv:
        we = sys.argv[sys.argv.index("--we") + 1]
    
    ours_path = BASE / "data" / "robots_history.jsonl"
    tw_path = BASE / "data" / f"tw_robots_{date}.jsonl"
    
    if "--ours" in sys.argv:
        idx = sys.argv.index("--ours")
        ours_path = Path(sys.argv[idx + 1])

    if not tw_path.exists():
        print(f"[!] нет файла {tw_path.name}")
        return
    if not ours_path.exists():
        print(f"[!] нет файла {ours_path.name}")
        return

    print(f"[tw_compare] эталон T-Widgets: {tw_path.name} (дата {date})")
    print(f"[tw_compare] наша история: {ours_path}")

    tw_events = load_tw_robots(tw_path)
    tw_ents = build_tw_entities(tw_events)
    
    # Фильтруем только те, что попадают в наш диапазон анализа (<= 60 сек)
    bursts = [e for e in tw_ents if e["period"] < 2.0 or (e["le"] - e["ls"]) < 4.0 or e["period"] > 60.0]
    real_tw = [e for e in tw_ents if e not in bursts]
    
    print(f"[tw_compare] роботов T-Widgets (уник. ticker+side+id): {len(tw_ents)}")
    print(f"[tw_compare] из них bursts/long (вне сравнения): {len(bursts)}")
    print(f"[tw_compare] реальных TW-роботов для сравнения: {len(real_tw)}")

    ours = load_tw_robots(ours_path) # Используем ту же функцию, она универсальна
    d0 = datetime.strptime(date, "%Y-%m-%d").date()
    w0 = datetime.combine(d0, datetime.strptime(ws, "%H:%M").time(), tzinfo=MSK).timestamp()
    w1 = datetime.combine(d0, datetime.strptime(we, "%H:%M").time(), tzinfo=MSK).timestamp()
    
    ours_windowed = [o for o in ours 
                     if 0 < float(o.get("interval_avg") or 0) <= 60.0
                     and w0 <= (our_window(o)[0] or -1) <= w1]
    print(f"[tw_compare] наших роботов (в мс) за {date}: {len(ours_windowed)}")

    by_key = defaultdict(list)
    for e in real_tw:
        by_key[(e["ticker"], e["side"])].append(e)

    admissible = [False] * len(ours_windowed)
    pairs = []
    for oi, o in enumerate(ours_windowed):
        our_st, our_en = our_window(o)
        our_iv = float(o["interval_avg"])
        our_qty = set(o.get("qty_variants", []))
        if not our_qty:
            continue
        for e in by_key.get((o["symbol"], o["side"].upper()), []):
            if abs(our_iv - e["period"]) > max(2.0, e["period"] * 0.2):
                continue
            if not any(e["lo"] <= q <= e["hi"] for q in our_qty):
                continue
            admissible[oi] = True
            pairs.append((window_overlap_score(our_st, our_en, e["ls"], e["le"]), oi, e["id"]))
    
    pairs.sort(key=lambda x: -x[0])
    matched = set()
    matched_ours = set()
    tp_list = []
    
    for score, oi, eid in pairs:
        if eid in matched or oi in matched_ours:
            continue
        matched.add(eid)
        matched_ours.add(oi)
        tw_ent = next(e for e in real_tw if e["id"] == eid)
        our_rec = ours_windowed[oi]
        our_st_ms = our_rec.get("start_ms") or ((our_rec.get("start_ts") or 0) * 1000)
        tw_st_ms = tw_ent["ls"] * 1000
        delay_ms = our_st_ms - tw_st_ms
        tp_list.append((our_rec, tw_ent, delay_ms))

    fp_list = [o for i, o in enumerate(ours_windowed) if not admissible[i]]
    fn_list = [e for e in real_tw if e["id"] not in matched]
    
    tp = len(tp_list)
    fp = len(fp_list)
    fn = len(fn_list)

    print("\n" + "=" * 70)
    print("СРАВНЕНИЕ: наша приблуда против T-Widgets (все времена в мс)")
    print("=" * 70)
    print(f"TP: {tp}   FP: {fp}   FN(реальные): {fn}   bursts(вне): {len(bursts)}")
    if tp + fp:
        print(f"Precision: {100.0*tp/(tp+fp):.1f}%   ", end="")
    if tp + fn:
        print(f"Recall: {100.0*tp/(tp+fn):.1f}%")
    print(f"METRICS: TP={tp} FP={fp} FN={fn}")

    print("\n--- TP (совпали, первые 30) ---")
    for our_rec, tw_ent, delay in tp_list[:30]:
        print(f"  {our_rec['symbol']:6s} {our_rec['side']:4s} наш int={our_rec['interval_avg']:.1f}s qty={our_rec['qty_variants']} | "
              f"tw int={tw_ent['period']:.1f}s qty=[{tw_ent['lo']:.0f},{tw_ent['hi']:.0f}] | "
              f"задержка: {delay:+.0f} мс")

    print("\n--- FP (наши, которых T-Widgets не видит, первые 30) ---")
    for o in fp_list[:30]:
        st, _ = our_window(o)
        t0 = datetime.fromtimestamp(st, MSK).strftime("%H:%M:%S")
        print(f"  {o['symbol']:6s} {o['side']:4s} int={float(o['interval_avg']):.1f}s "
              f"qty={o['qty_variants']} повт={o['repeats']} окно {t0}-{datetime.fromtimestamp((our_window(o)[1] or st), MSK).strftime('%H:%M:%S')}")

    print("\n--- FN (T-Widgets видит, мы нет; сортировка по интервалу вниз, первые 30) ---")
    fn_sorted = sorted(fn_list, key=lambda x: -x["period"])
    for e in fn_sorted[:30]:
        t0 = datetime.fromtimestamp(e["ls"], MSK).strftime("%H:%M:%S")
        t1 = datetime.fromtimestamp(e["le"], MSK).strftime("%H:%M:%S")
        print(f"  {e['ticker']:6s} {e['side']:4s} int={e['period']:.1f}s qty=[{e['lo']:.0f},{e['hi']:.0f}] "
              f"count={e['op']} окно {t0}-{t1}")

    print("\n[tw_compare] Готово.")

if __name__ == "__main__":
    main()