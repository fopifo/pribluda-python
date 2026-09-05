"""
Приблуда на python — СТРИМИНГ-детектор сеток (живая детекция).
Кормит сделки в хронологическом порядке (как в бою), держит скользящий
буфер timestamps per (тикер,сторона,qty) и выдаёт сигнал СРАЗУ, когда
хвост из >=min_repeats ударов лёг на сетку с периодом p.

Метрика: Recall_strict против aniscan + задержка детекции (сек от старта).

Использование:
    python research/replay_grid.py 2026-09-04
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import defaultdict, deque

BASE = Path(__file__).resolve().parent.parent
MSK = timezone(timedelta(hours=3))
MIN_REPEATS = 4
BUFFER = 60
P_MIN, P_MAX = 2.0, 120.0


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
    with open(BASE / "data" / "aniscan_history.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            rid = (r.get("robot") or {}).get("id")
            if not rid:
                continue
            g = robots.setdefault(rid, {
                "ticker": r.get("ticker"), "side": (r.get("operationType") or "").lower(),
                "period": float(r.get("period") or 0), "qmin": r.get("minLot") or 0,
                "qmax": r.get("maxLot") or 0, "ins": None, "del": None})
            ts = iso(r.get("createDttm"))
            if r.get("eventType") == "INSERT":
                g["ins"] = ts if g["ins"] is None else min(g["ins"], ts)
            elif r.get("eventType") == "DELETE":
                g["del"] = ts if g["del"] is None else max(g["del"], ts)
    return [g for g in robots.values()
            if g["ins"] is not None and not ((g["del"] or 0) < day0 or g["ins"] > day1)]


class StreamGrid:
    def __init__(self):
        self.buf = defaultdict(lambda: deque(maxlen=BUFFER))
        self.emitted = {}

    def on_trade(self, sym, side, qty, ts):
        b = self.buf[(sym, side, qty)]
        b.append(ts)
        if len(b) < MIN_REPEATS:
            return None
        gaps = [(b[i + 1] - b[i]) / 1000 for i in range(len(b) - 1)]
        small = sorted(g for g in gaps if 1.0 <= g <= 120.0)
        if len(small) < 3:
            return None
        p = small[len(small) // 2]
        if not (P_MIN <= p <= P_MAX):
            return None
        # хвост: последние MIN_REPEATS ударов кратны p, из них >=2 одиночных
        tail = gaps[-(MIN_REPEATS - 1):]
        single = 0
        for g in tail:
            k = round(g / p)
            if k < 1 or abs(g - k * p) > max(k * p * 0.12, 0.7):
                return None
            if k == 1:
                single += 1
        if single < 2:
            return None
        key = (sym, side, qty)
        last = self.emitted.get(key)
        if last is not None and ts - last < 4 * p * 1000:
            return None
        self.emitted[key] = ts
        return {"ticker": sym, "side": side, "qty": qty, "period": p, "ts": ts}


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else "2026-09-04"
    an = load_aniscan(date_str)
    trades = []
    for fp in sorted((BASE / "data").glob(f"*_{date_str}.json")):
        sym = fp.name.replace(f"_{date_str}.json", "")
        try:
            data = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        for t in data:
            side = (t.get("side") or "").lower()
            q, ts = t.get("qty"), t.get("timestamp")
            if side in ("buy", "sell") and q and ts:
                trades.append((float(ts), sym, side, int(q)))
    trades.sort()
    sg = StreamGrid()
    dets = []
    for ts, sym, side, qty in trades:
        d = sg.on_trade(sym, side, qty, ts)
        if d:
            dets.append(d)
    matched, ex = 0, []
    for g in an:
        hit = None
        for w in dets:
            if w["ticker"] != g["ticker"] or w["side"] != g["side"]:
                continue
            if not (g["ins"] or 0) <= w["ts"] <= (g["del"] or 0) + 120000:
                continue
            if g["period"] > 0 and not (0.7 <= w["period"] / g["period"] <= 1.3):
                continue
            if not (g["qmin"] * 0.5 <= w["qty"] <= g["qmax"] * 1.5):
                continue
            hit = w
            break
        if hit:
            matched += 1
            delay = (hit["ts"] - g["ins"]) / 1000
            if len(ex) < 15:
                ex.append(f"  {g['ticker']:6} {g['side']:4} aniscan int={g['period']:.0f}s | "
                          f"мы int={hit['period']:.0f}s qty={hit['qty']} задержка={delay:.0f}с")
    print(f"aniscan: {len(an)}; живых сигналов: {len(dets)}; совпало: {matched}")
    print(f"Recall_strict (живой): {matched / len(an):.1%}")
    print("\n".join(ex))


if __name__ == "__main__":
    main()