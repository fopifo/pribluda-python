"""
Приблуда на python — честные метрики против СТРОГОГО эталона aniscan.ru:
наша replay-история (data/replay_alor_history.jsonl) против
data/aniscan_history.jsonl за дату.

Метрики:
  Recall_strict    = matched / aniscan   (сколько строгих роботов ловим)
  Precision_strict = matched / ours      (сколько наших подтверждает aniscan)

Использование:
    python research/compare_aniscan_ours.py 2026-09-04
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
MSK = timezone(timedelta(hours=3))
TIME_TOL_MS = 120_000
INT_LO, INT_HI = 0.7, 1.3
QTY_TOL = 0.5


def iso_to_ms(s):
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp() * 1000
    except (ValueError, TypeError):
        return None


def fmt_ms(ms):
    if ms is None:
        return "--:--:--"
    return datetime.fromtimestamp(ms / 1000, tz=MSK).strftime("%H:%M")


def load_aniscan(date_str):
    day0 = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=MSK)
    day0_ms = day0.timestamp() * 1000
    day1_ms = day0_ms + 86400_000
    robots = {}
    with open(DATA / "aniscan_history.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            rid = (rec.get("robot") or {}).get("id")
            if not rid:
                continue
            r = robots.setdefault(rid, {
                "ticker": rec.get("ticker"),
                "side": (rec.get("operationType") or "").lower(),
                "period": float(rec.get("period") or 0),
                "qmin": rec.get("minLot") or 0,
                "qmax": rec.get("maxLot") or 0,
                "ins": None, "del": None})
            ts = iso_to_ms(rec.get("createDttm"))
            if rec.get("eventType") == "INSERT":
                r["ins"] = ts if r["ins"] is None else min(r["ins"], ts)
            elif rec.get("eventType") == "DELETE":
                r["del"] = ts if r["del"] is None else max(r["del"], ts)
    out = []
    now_ms = datetime.now(MSK).timestamp() * 1000
    for r in robots.values():
        start = r["ins"]
        end = r["del"] or now_ms
        if start is None or end < day0_ms or start > day1_ms:
            continue
        r["start_ms"], r["end_ms"] = start, end
        out.append(r)
    return out


def load_ours(date_str):
    out = []
    p = DATA / "replay_alor_history.jsonl"
    if not p.exists():
        print(f"Нет {p.name} — сначала: python research/replay_alor.py {date_str}")
        sys.exit(1)
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            start_ms = r.get("start_ms")
            end_ms = r.get("end_ms")
            interval_ms = r.get("interval_ms")
            if start_ms is None or end_ms is None or interval_ms is None:
                continue
            day = datetime.fromtimestamp(start_ms / 1000, tz=MSK).date().isoformat()
            if day != date_str:
                continue
            qv = r.get("qty_variants") or []
            if not qv:
                continue
            out.append({
                "ticker": r.get("symbol"),
                "side": r.get("side"),
                "interval_ms": float(interval_ms),
                "qty_min": min(qv),
                "qty_max": max(qv),
                "start_ms": int(start_ms),
                "end_ms": int(end_ms),
                "repeats": r.get("repeats"),
            })
    return out


def match(a, w):
    if a["ticker"] != w["ticker"] or a["side"] != w["side"]:
        return False
    if a["start_ms"] > w["end_ms"] + TIME_TOL_MS:
        return False
    if w["start_ms"] > a["end_ms"] + TIME_TOL_MS:
        return False
    ai = a["period"] * 1000
    if ai > 0 and w["interval_ms"] > 0:
        ratio = ai / w["interval_ms"]
        if not (INT_LO <= ratio <= INT_HI):
            return False
    a_lo = a["qmin"] * (1 - QTY_TOL)
    a_hi = a["qmax"] * (1 + QTY_TOL)
    if not (a_lo <= w["qty_max"] and w["qty_min"] <= a_hi):
        return False
    return True


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else "2026-09-04"
    an = load_aniscan(date_str)
    ours = load_ours(date_str)
    print(f"[compare] aniscan роботов: {len(an)}; наших роботов: {len(ours)}")

    used = set()
    matched, an_only = [], []
    for a in an:
        hit = None
        for i, w in enumerate(ours):
            if i in used:
                continue
            if match(a, w):
                hit = i
                break
        if hit is not None:
            used.add(hit)
            matched.append((a, ours[hit]))
        else:
            an_only.append(a)

    rec = len(matched) / len(an) if an else 0.0
    prec = len(matched) / len(ours) if ours else 0.0
    print(f"Совпали: {len(matched)}")
    print(f"Recall_strict (ловим строгих): {rec:.1%}")
    print(f"Precision_strict (наших подтверждено): {prec:.1%}")

    print("--- совпали (первые 20) ---")
    for a, w in matched[:20]:
        print(f"  {a['ticker']:6} {a['side']:4} aniscan int={a['period']:.0f}s "
              f"qty=[{a['qmin']},{a['qmax']}] | мы int={w['interval_ms']/1000:.0f}s "
              f"qty=[{w['qty_min']},{w['qty_max']}] повт={w['repeats']}")
    print("--- aniscan видит, мы нет (первые 20) ---")
    for a in an_only[:20]:
        print(f"  {a['ticker']:6} {a['side']:4} int={a['period']:.0f}s "
              f"qty=[{a['qmin']},{a['qmax']}] окно {fmt_ms(a['start_ms'])}-{fmt_ms(a['end_ms'])}")


if __name__ == "__main__":
    main()