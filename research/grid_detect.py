"""
Приблуда на python — офлайн-реконструкция сеток роботов («собирать и сортировать»).
Для каждого (тикер, сторона, qty) собирает ВСЕ timestamps из data/<T>_<дата>.json,
сортирует, берёт медиану малых зазоров как период, считает подряд идущие удары.
Сравнивает с aniscan и даёт честный Recall_strict офлайн-метода.

Использование:
    python research/grid_detect.py 2026-09-04
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import defaultdict

BASE = Path(__file__).resolve().parent.parent
MSK = timezone(timedelta(hours=3))


def iso(s):
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp() * 1000
    except (ValueError, TypeError):
        return None


def load_aniscan(date_str):
    day0 = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=MSK).timestamp() * 1000
    day1 = day0 + 86400_000
    robots = {}
    p = BASE / "data" / "aniscan_history.jsonl"
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            rid = (r.get("robot") or {}).get("id")
            if not rid:
                continue
            g = robots.setdefault(rid, {
                "ticker": r.get("ticker"),
                "side": (r.get("operationType") or "").lower(),
                "period": float(r.get("period") or 0),
                "qmin": r.get("minLot") or 0,
                "qmax": r.get("maxLot") or 0,
                "ins": None, "del": None})
            ts = iso(r.get("createDttm"))
            if r.get("eventType") == "INSERT":
                g["ins"] = ts if g["ins"] is None else min(g["ins"], ts)
            elif r.get("eventType") == "DELETE":
                g["del"] = ts if g["del"] is None else max(g["del"], ts)
    return [g for g in robots.values()
            if g["ins"] is not None and not ((g["del"] or 0) < day0 or g["ins"] > day1)]


def detect_grids(date_str):
    out = []
    for fp in sorted((BASE / "data").glob(f"*_{date_str}.json")):
        sym = fp.name.replace(f"_{date_str}.json", "")
        try:
            data = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        by = defaultdict(list)
        for t in data:
            side = (t.get("side") or "").lower()
            q = t.get("qty")
            ts = t.get("timestamp")
            if side in ("buy", "sell") and q and ts:
                by[(side, int(q))].append(float(ts))
        for (side, q), tsl in by.items():
            if len(tsl) < 6:
                continue
            tsl.sort()
            gaps = [(tsl[i + 1] - tsl[i]) / 1000 for i in range(len(tsl) - 1)]
            small = sorted(g for g in gaps if 1.0 <= g <= 120.0)
            if not small:
                continue
            p = small[len(small) // 2]
            if p < 2.0:
                continue
            runs, cur, s0 = [], 1, tsl[0]
            for i in range(1, len(tsl)):
                g = (tsl[i] - tsl[i - 1]) / 1000
                k = round(g / p)
                if k >= 1 and abs(g - k * p) <= max(k * p * 0.12, 0.7):
                    cur += 1
                else:
                    runs.append((cur, s0, tsl[i - 1]))
                    cur, s0 = 1, tsl[i]
            runs.append((cur, s0, tsl[-1]))
            for cnt, s, e in runs:
                if cnt >= 4:
                    out.append({"ticker": sym, "side": side, "qty": q,
                                "period": p, "count": cnt, "start": s, "end": e})
    return out


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else "2026-09-04"
    an = load_aniscan(date_str)
    ours = detect_grids(date_str)
    matched, ex = 0, []
    for g in an:
        hit = None
        for w in ours:
            if w["ticker"] != g["ticker"] or w["side"] != g["side"]:
                continue
            if w["start"] > (g["del"] or 0) + 120000 or g["ins"] > w["end"] + 120000:
                continue
            if g["period"] > 0 and not (0.7 <= w["period"] / g["period"] <= 1.3):
                continue
            if not (g["qmin"] * 0.5 <= w["qty"] <= g["qmax"] * 1.5):
                continue
            hit = w
            break
        if hit:
            matched += 1
            if len(ex) < 15:
                ex.append(f"  {g['ticker']:6} {g['side']:4} aniscan int={g['period']:.0f}s "
                          f"qty=[{g['qmin']},{g['qmax']}] | мы int={hit['period']:.0f}s "
                          f"qty={hit['qty']} count={hit['count']}")
    print(f"aniscan: {len(an)}; наших сеток: {len(ours)}; совпало: {matched}")
    print(f"Recall_strict: {matched / len(an):.1%}")
    print("\n".join(ex))


if __name__ == "__main__":
    main()