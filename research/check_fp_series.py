"""
Приблуда на python — ручная проверка FP-серии: показать реальные сделки
из data/quik_trades.csv в заданном окне, чтобы глазами отличить робота
(периодичность, стабильный объём) от случайных заявок (шум).

Использование (из корня):
    python research/check_fp_series.py <TICKER> <HH:MM:SS> <HH:MM:SS> <QMIN> <QMAX> <side>
Пример:
    python research/check_fp_series.py GMKN 11:40:59 11:46:08 15 25 buy

Печатает сделки указанной стороны с объёмом в [QMIN,QMAX] и зазор (gap)
от предыдущей такой сделки — по зазорам видна периодичность.
Полное окно (обе стороны) пишется в output/check_fp_<TICKER>.txt.
НИЧЕГО не меняет — только читает.
"""
import sys
import time as _time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
CSV = BASE / "data" / "quik_trades.csv"
OUTDIR = BASE / "output"
MSK = ZoneInfo("Europe/Moscow")
DATE = "2026-09-03"


class Progress:
    def __init__(self, total, label):
        self.total = max(total, 1)
        self.label = label
        self.done = 0
        self.t0 = _time.time()
        self.last = 0.0

    def update(self, n):
        self.done += n
        now = _time.time()
        if now - self.last < 0.25 and self.done < self.total:
            return
        self.last = now
        pct = min(self.done * 100 // self.total, 100)
        dt = max(now - self.t0, 1e-9)
        speed = self.done / dt
        eta = (self.total - self.done) / max(speed, 1.0)
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        print(f"\r[check_fp] {self.label} [{bar}] {pct:3d}% | "
              f"{self.done // 1048576}/{self.total // 1048576}MB | "
              f"{speed / 1048576:.0f}MB/s | ETA {eta:.0f}s", end="", flush=True)

    def close(self):
        print()


def to_ms(hms):
    dt = datetime.fromisoformat(f"{DATE}T{hms}")
    return int(dt.replace(tzinfo=MSK).timestamp() * 1000)


def fmt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=MSK).strftime("%H:%M:%S")


def norm_symbol(s):
    return s.split(".")[0].split("@")[0]


def main():
    if len(sys.argv) != 7:
        print(__doc__)
        sys.exit(1)
    ticker, h_start, h_end, qmin_s, qmax_s, side = sys.argv[1:7]
    qmin, qmax = float(qmin_s), float(qmax_s)
    t0, t1 = to_ms(h_start), to_ms(h_end)

    hits, gaps, total_window = [], [], 0
    last_hit_ms = None
    out_lines = []

    p = Progress(CSV.stat().st_size, "csv")
    with open(CSV, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            p.update(len(line))
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(";")
            if len(parts) < 5:
                continue
            if norm_symbol(parts[0]) != ticker:
                continue
            try:
                ts = int(float(parts[4]))
            except (ValueError, IndexError):
                continue
            if ts < t0 or ts > t1:
                continue
            try:
                qty = float(parts[1])
                price = parts[2]
                sd = parts[3]
            except (ValueError, IndexError):
                continue
            total_window += 1
            out_lines.append(f"{fmt(ts)} {sd:<4} qty={qty:<8} price={price}")
            if sd == side and qmin <= qty <= qmax:
                if last_hit_ms is not None:
                    gaps.append(round((ts - last_hit_ms) / 1000, 1))
                last_hit_ms = ts
                hits.append((ts, qty, price))
    p.close()

    OUTDIR.mkdir(exist_ok=True)
    full = OUTDIR / f"check_fp_{ticker}.txt"
    full.write_text("\n".join(out_lines), encoding="utf-8")

    print(f"\n=== {ticker} {side} qty[{qmin:g},{qmax:g}] окно {h_start}-{h_end} ===")
    print(f"Всего сделок в окне (обе стороны): {total_window}")
    print(f"Сделок нашей стороны с нужным объёмом: {len(hits)}")
    print("--- сделки и зазоры (gap, с) ---")
    prev = None
    for ts, qty, price in hits:
        g = f"gap={(ts - prev) / 1000:.1f}s" if prev is not None else "gap=-"
        print(f"  {fmt(ts)} qty={qty:<8} price={price:<10} {g}")
        prev = ts
    if gaps:
        print(f"--- зазоры: min={min(gaps)} max={max(gaps)} "
              f"median={sorted(gaps)[len(gaps) // 2]} ---")
    print(f"Полное окно: {full}")


if __name__ == "__main__":
    main()