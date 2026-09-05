"""
Приблуда на python — диагностика FN: почему T-Widgets видит робота, а мы нет.
Читает data/<TICKER>_YYYY-MM-DD.json (Алор, истинные стороны), выводит ВСЕ
сделки указанной стороны в окне, помечает те, что в диапазоне объёма [QMIN,QMAX],
и считает зазоры между ними (чистая ли сетка интервала или её ломает шум).

Использование:
    python research/check_fn_series.py ГГГГ-ММ-ДД TICKER side ЧЧ:ММ:СС ЧЧ:ММ:СС QMIN QMAX
Пример:
    python research/check_fn_series.py 2026-09-03 OZON sell 10:04:20 10:10:20 5 6
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
MSK = ZoneInfo("Europe/Moscow")


class Progress:
    def __init__(self, total, label):
        self.total = max(int(total), 1)
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
        print(f"\r[check_fn] {self.label} [{bar}] {pct:3d}% | "
              f"{self.done}/{self.total} | {speed:.0f}/s | ETA {eta:.0f}s",
              end="", flush=True, file=sys.stderr)

    def close(self):
        print(file=sys.stderr)


def to_ms(hms):
    return int(datetime.fromisoformat(f"{DATE}T{hms}").replace(tzinfo=MSK).timestamp() * 1000)


def fmt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=MSK).strftime("%H:%M:%S")


def main():
    global DATE
    if len(sys.argv) != 8:
        print(__doc__)
        sys.exit(1)
    DATE, ticker, side, h0, h1, qmin_s, qmax_s = sys.argv[1:8]
    qmin, qmax = float(qmin_s), float(qmax_s)
    t0, t1 = to_ms(h0), to_ms(h1)

    path = DATA / f"{ticker}_{DATE}.json"
    if not path.exists():
        print(f"[check_fn] Нет файла {path}. Скачай: python tools/alor_download_day.py {DATE} {ticker}")
        sys.exit(1)

    print(f"[check_fn] читаю {path.name}...")
    with open(path, "r", encoding="utf-8") as f:
        trades = json.load(f)

    sel = [t for t in trades
           if t.get("side") == side
           and t0 <= (t.get("timestamp") or 0) <= t1]
    sel.sort(key=lambda t: t.get("timestamp") or 0)

    in_range = [t for t in sel if qmin <= float(t.get("qty", 0)) <= qmax]

    print(f"\n=== {ticker} {side} окно {h0}-{h1} ===")
    print(f"Всего сделок стороны: {len(sel)}; в объёме [{qmin:g},{qmax:g}]: {len(in_range)}")
    print("--- все сделки ( * = в диапазоне объёма ) ---")
    p = Progress(len(sel), "scan")
    prev = None
    for t in sel:
        p.update(1)
        ts = t.get("timestamp") or 0
        qty = float(t.get("qty", 0))
        mark = "*" if qmin <= qty <= qmax else " "
        gap = f"gap={(ts - prev) / 1000:.1f}s" if prev is not None else "gap=-"
        print(f"  {mark} {fmt(ts)} qty={qty:<8} price={t.get('price')} {gap}")
        prev = ts
    p.close()

    print("--- зазоры между сделками В диапазоне (сетка робота) ---")
    prev = None
    gaps = []
    for t in in_range:
        ts = t.get("timestamp") or 0
        if prev is not None:
            g = (ts - prev) / 1000
            gaps.append(g)
            print(f"  {fmt(ts)} qty={float(t.get('qty',0)):<8} gap={g:.1f}s")
        prev = ts
    if gaps:
        print(f"зaзоры: min={min(gaps):.1f} max={max(gaps):.1f} "
              f"median={sorted(gaps)[len(gaps)//2]:.1f}")


if __name__ == "__main__":
    main()