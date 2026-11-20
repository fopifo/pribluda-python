"""
aggregate_robots.py — агрегация эпизодов в долгоживущие сущности (роботов).
Берёт короткие серии из replay_alor_history.jsonl, склеивает эпизоды одного
робота (совместимые interval+qty, разрыв <= MAX_GAP) в одну сущность и применяет
формулу из reverse_engineer: робот = dur>=DUR_MIN and n>=N_MIN.
Пишет ТОЛЬКО роботов в data/replay_agg_history.jsonl.
Запуск: python research/aggregate_robots.py 2026-11-18
Сравнение: python research/tw_compare.py 2026-11-18 --ours data/replay_agg_history.jsonl
"""
import sys, json, time, statistics
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).resolve().parent.parent
HIST_IN = BASE / "data" / "replay_alor_history.jsonl"
HIST_OUT = BASE / "data" / "replay_agg_history.jsonl"

MAX_GAP = 3600.0     # макс разрыв между эпизодами одного робота (сек)
DUR_MIN = 1800.0     # минимальная жизнь робота (сек) = 30 мин
N_MIN = 60           # минимальное суммарное число сделок
INT_RATIO = (0.8, 1.25)
QTY_RATIO = (0.6, 1.6)


class Progress:
    def __init__(self, total, label):
        self.total = max(int(total), 1); self.label = label; self.done = 0
        self.t0 = time.time(); self.last = 0.0; self.lp = -1; self.tty = sys.stderr.isatty()
    def _line(self):
        pct = min(self.done * 100 // self.total, 100); dt = max(time.time() - self.t0, 1e-9)
        sp = self.done / dt; eta = (self.total - self.done) / max(sp, 1)
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        return f"[agg] {self.label} [{bar}] {pct:3d}% | {self.done}/{self.total} | ETA {eta:.0f}s"
    def update(self, n=1):
        self.done += n; pct = min(self.done * 100 // self.total, 100); now = time.time()
        if self.tty:
            if now - self.last < 0.25 and self.done < self.total: return
            self.last = now; sys.stderr.write("\r" + self._line()); sys.stderr.flush()
        else:
            if pct // 10 != self.lp // 10:
                self.lp = pct; print(self._line(), file=sys.stderr, flush=True)
    def close(self):
        if self.tty: sys.stderr.write("\r" + self._line() + "\n")
        else: print(self._line(), file=sys.stderr, flush=True)


def med_qty(rec):
    qv = rec.get("qty_variants") or [0]
    return statistics.median(qv)


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else "2026-11-18"
    if not HIST_IN.exists():
        print(f"[!] нет {HIST_IN.name}"); return
    eps = [json.loads(l) for l in HIST_IN.read_text(encoding="utf-8").splitlines() if l.strip()]
    eps = [e for e in eps if 0 < float(e.get("interval_avg", 0)) <= 60.0]
    groups = defaultdict(list)
    for e in eps:
        groups[(e["symbol"], e["side"].lower())].append(e)

    entities = []
    pr = Progress(len(groups), "aggregate")
    for key, lst in groups.items():
        pr.update()
        lst.sort(key=lambda x: x.get("start_ts", 0))
        clusters = []
        for e in lst:
            st = e.get("start_ts") or 0.0
            en = e.get("end_ts") or st
            iv = float(e["interval_avg"]); qm = med_qty(e)
            placed = False
            for c in clusters:
                if not (INT_RATIO[0] <= iv / c["iv"] <= INT_RATIO[1]):
                    continue
                if c["qm"] > 0 and qm > 0 and not (QTY_RATIO[0] <= qm / c["qm"] <= QTY_RATIO[1]):
                    continue
                if (st - c["end"]) > MAX_GAP:
                    continue
                c["eps"].append(e); c["end"] = max(c["end"], en)
                c["start"] = min(c["start"], st); c["n"] += int(e.get("repeats", 0))
                placed = True
                break
            if not placed:
                clusters.append({"iv": iv, "qm": qm, "start": st, "end": en,
                                 "eps": [e], "n": int(e.get("repeats", 0))})
        for c in clusters:
            ivs = [float(x["interval_avg"]) for x in c["eps"]]
            qv = []
            for x in c["eps"]:
                for q in (x.get("qty_variants") or []):
                    if q not in qv: qv.append(q)
            entities.append({
                "symbol": key[0], "side": key[1],
                "interval_avg": round(statistics.median(ivs), 2),
                "repeats": c["n"],
                "qty_variants": qv[:6],
                "start_ts": c["start"], "end_ts": c["end"],
                "start_ms": int(c["start"] * 1000), "end_ms": int(c["end"] * 1000),
                "dur": c["end"] - c["start"],
                "preset": "agg",
            })
    pr.close()

    robots = [e for e in entities if e["dur"] >= DUR_MIN and e["repeats"] >= N_MIN]
    robots.sort(key=lambda x: -x["dur"])
    if HIST_OUT.exists(): HIST_OUT.unlink()
    with open(HIST_OUT, "a", encoding="utf-8") as f:
        for r in robots:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"[agg] эпизодов={len(eps)} сущностей={len(entities)} роботов(dur>={DUR_MIN/60:.0f}м,n>={N_MIN})={len(robots)}")
    print(f"{'sym':7s}{'side':5s}{'int':>7s}{'dur_м':>8s}{'n':>7s}  вердикт")
    for e in sorted(entities, key=lambda x: -x["dur"])[:25]:
        verdict = "РОБОТ" if (e["dur"] >= DUR_MIN and e["repeats"] >= N_MIN) else "шум"
        print(f"{e['symbol']:7s}{e['side']:5s}{e['interval_avg']:7.1f}{e['dur']/60:8.1f}{e['repeats']:7d}  {verdict}")
    print(f"[agg] -> {HIST_OUT}")


if __name__ == "__main__":
    main()