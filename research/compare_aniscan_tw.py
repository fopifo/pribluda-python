"""
Приблуда на python — перекрёстная сверка двух эталонов за дату:
aniscan.ru (data/aniscan_history.jsonl) против T-Widgets
(data/tw_robots_YYYY-MM-DD.jsonl).

Цель: каких роботов видят ОБА скринера (сильный эталон), а каких
только один (артефакты, как PLZL sell 1425).

Использование:
    python research/compare_aniscan_tw.py 2026-09-04
"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
MSK = timezone(timedelta(hours=3))
TIME_TOL_MS = 120_000
INT_LO, INT_HI = 0.7, 1.3
QTY_TOL = 0.5
MIN_INTERVAL_MS = 2000.0
MIN_COUNT = 4


class Progress:
    def __init__(self, total, label):
        self.total = max(total, 1)
        self.label = label
        self.done = 0
        self.t0 = time.time()
        self.last = 0.0

    def update(self, n):
        self.done += n
        now = time.time()
        if now - self.last < 0.25 and self.done < self.total:
            return
        self.last = now
        pct = min(self.done * 100 // self.total, 100)
        dt = max(now - self.t0, 1e-9)
        speed = self.done / dt
        eta = (self.total - self.done) / max(speed, 1.0)
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        print(f"\r[compare] {self.label} [{bar}] {pct:3d}% | "
              f"{self.done // 1024}/{self.total // 1024}KB | "
              f"{speed / 1024:.0f}KB/s | ETA {eta:.0f}s", end="", flush=True)

    def close(self):
        print()


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
    return datetime.fromtimestamp(ms / 1000, tz=MSK).strftime("%m-%d %H:%M")


def load_aniscan(date_str):
    day0 = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=MSK)
    day0_ms = day0.timestamp() * 1000
    day1_ms = day0_ms + 86400_000
    robots = {}
    p = DATA / "aniscan_history.jsonl"
    with open(p, encoding="utf-8") as f:
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
        if start is None:
            continue
        if end < day0_ms or start > day1_ms:
            continue
        r["start_ms"], r["end_ms"] = start, end
        out.append(r)
    return out


def load_tw(path):
    out = {}
    pbar = Progress(path.stat().st_size, "tw ")
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            pbar.update(len(line))
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            payload = rec.get("payload") or {}
            for rob in payload.get("robots") or []:
                ticker = rob.get("ticker")
                if not ticker:
                    continue
                for state in ("active", "completed"):
                    for a in rob.get(state) or []:
                        end_ms = iso_to_ms(a.get("end"))
                        key = (ticker, "buy" if a.get("isBuy") else "sell", a.get("id"))
                        old = out.get(key)
                        if old is not None and (old["end_ms"] or 0) >= (end_ms or 0):
                            continue
                        out[key] = {
                            "ticker": ticker,
                            "side": key[1],
                            "interval_ms": float(a.get("interval") or 0),
                            "qty_min": a.get("minLots") or 0,
                            "qty_max": a.get("maxLots") or 0,
                            "start_ms": iso_to_ms(a.get("start")),
                            "end_ms": end_ms,
                            "count": a.get("count"),
                        }
    pbar.close()
    return list(out.values())


def match(a, w):
    if a["ticker"] != w["ticker"] or a["side"] != w["side"]:
        return False
    if a["start_ms"] > (w["end_ms"] or 0) + TIME_TOL_MS:
        return False
    if (w["start_ms"] or 0) > a["end_ms"] + TIME_TOL_MS:
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
    twf = DATA / f"tw_robots_{date_str}.jsonl"
    if not twf.exists():
        print(f"Нет {twf.name}")
        sys.exit(1)
    tw = [w for w in load_tw(twf)
          if w["interval_ms"] >= MIN_INTERVAL_MS and (w["count"] or 0) >= MIN_COUNT]
    print(f"[compare] aniscan роботов за {date_str}: {len(an)}; TW реальных: {len(tw)}")

    used = set()
    matched, an_only = [], []
    for a in an:
        hit = None
        for i, w in enumerate(tw):
            if i in used:
                continue
            if match(a, w):
                hit = i
                break
        if hit is not None:
            used.add(hit)
            matched.append((a, tw[hit]))
        else:
            an_only.append(a)

    print(f"Видят ОБА эталона: {len(matched)}")
    print(f"Только aniscan: {len(an_only)}")
    print("--- только aniscan (первые 20) ---")
    for a in an_only[:20]:
        print(f"  {a['ticker']:6} {a['side']:4} int={a['period']:.0f}s "
              f"qty=[{a['qmin']},{a['qmax']}] окно {fmt_ms(a['start_ms'])}-{fmt_ms(a['end_ms'])}")
    print("--- совпали (первые 20) ---")
    for a, w in matched[:20]:
        print(f"  {a['ticker']:6} {a['side']:4} aniscan int={a['period']:.0f}s "
              f"qty=[{a['qmin']},{a['qmax']}] | tw int={w['interval_ms']/1000:.0f}s "
              f"qty=[{w['qty_min']},{w['qty_max']}]")
    plzl = [a for a in an if a["ticker"] == "PLZL" and a["side"] == "sell"]
    print(f"PLZL sell в aniscan: {len(plzl)}")
    for a in plzl[:5]:
        print(f"  int={a['period']:.0f}s qty=[{a['qmin']},{a['qmax']}] "
              f"окно {fmt_ms(a['start_ms'])}-{fmt_ms(a['end_ms'])}")


if __name__ == "__main__":
    main()