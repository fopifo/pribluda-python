"""
research/market_structure.py — скринер микроструктуры (приблуда).
Детекторы: ПЛОТНОСТЬ, АЙСБЕРГ, ПЛАНКА(эпизод), СПРЕД-РОБОТ(эпизод).
По умолчанию фильтр по РАБОЧИМ тикерам владельца (mini_window_config.json),
SBER не входит. --all = все тикеры; --tickers A,B,C = свой список.
Планки и спред-роботы агрегируются в ЭПИЗОДЫ (стенка поставлена->снята = 1),
а не считаются по каждому тику стакана (иначе сотни тысяч мусора).
Источники: data/orderbook_DATE.jsonl + data/pbsdk_trades_DATE.jsonl.
Отчёт: output/market_structure_DATE.txt (один файл).
Прогресс-бары на всех фазах.
"""
import sys
import json
import re
import time
import bisect
import argparse
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
MSK = ZoneInfo("Europe/Moscow")

ABS_MIN = {"MTLR": 2000, "MTLRP": 500, "CNYRUBF": 2000, "CNY-12.26": 200,
           "CNYRUB_TOM": 2000}
DEF_ABS = 1000
EPISODE_GAP_MS = 2000


def abs_min(sym, scale):
    return ABS_MIN.get(sym, DEF_ABS) * scale


def load_default_tickers():
    p = BASE / "mini_window_config.json"
    s = set()
    if p.exists():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8-sig"))
            for key in ("top_row", "bottom_row"):
                for cell in cfg.get(key, []):
                    if cell:
                        for t in re.split(r"[,\s]+", str(cell)):
                            if t:
                                s.add(t.upper())
        except Exception:
            pass
    return s


class Bar:
    def __init__(self, total, label):
        self.total = max(total, 1)
        self.label = label
        self.n = 0
        self.t0 = time.time()
        self.tty = sys.stdout.isatty()
        self.last = 0.0
        self.lastpct = -1

    def add(self, k=1):
        self.n += k
        now = time.time()
        if now - self.last < 0.2 and self.n < self.total:
            return
        self.last = now
        self._render()

    def _render(self):
        pct = 100.0 * self.n / self.total
        filled = int(pct // 5)
        el = time.time() - self.t0
        sp = self.n / el if el > 0 else 0
        eta = (self.total - self.n) / sp if sp > 0 else 0
        line = (f"[{'#' * filled}{'.' * (20 - filled)}] {pct:3.0f}% | "
                f"{self.n}/{self.total} | {sp:.0f}/с | ETA {eta:.0f}с")
        if self.tty:
            sys.stdout.write("\r" + self.label + " " + line)
            sys.stdout.flush()
        else:
            if int(pct) // 10 != self.lastpct // 10:
                print(self.label + " " + line)
            self.lastpct = int(pct)

    def done(self):
        self.n = self.total
        self._render()
        if self.tty:
            sys.stdout.write("\n")


def hhmmss(ms):
    return datetime.fromtimestamp(ms / 1000.0, MSK).strftime("%H:%M:%S")


def pkey(p):
    return round(float(p), 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--tickers", default=None)
    ap.add_argument("--window", type=float, default=120.0)
    ap.add_argument("--iceberg-ratio", type=float, default=3.0)
    ap.add_argument("--iceberg-min-exec", type=float, default=500.0)
    ap.add_argument("--density-ratio", type=float, default=5.0)
    ap.add_argument("--density-hold", type=float, default=20.0)
    ap.add_argument("--density-win", type=int, default=5)
    ap.add_argument("--plank-max-dt", type=float, default=2.0)
    ap.add_argument("--spread-gap", type=float, default=2000.0)
    ap.add_argument("--abs-scale", type=float, default=1.0)
    ap.add_argument("--top", type=int, default=30)
    a = ap.parse_args()

    if a.tickers:
        allowed = {t.strip().upper() for t in a.tickers.split(",") if t.strip()}
    elif a.all:
        allowed = None
    else:
        allowed = load_default_tickers() or None

    data = BASE / "data"
    if a.date:
        date = a.date
    else:
        files = sorted(data.glob("orderbook_2*.jsonl"))
        if not files:
            print("[!] нет файлов orderbook_*.jsonl в data/")
            return
        date = files[-1].stem.replace("orderbook_", "")
    ob_path = data / f"orderbook_{date}.jsonl"
    tr_path = data / f"pbsdk_trades_{date}.jsonl"
    if not ob_path.exists():
        print(f"[!] нет {ob_path.name}")
        return
    have_trades = tr_path.exists()
    print(f"[*] фильтр тикеров: {sorted(allowed) if allowed else 'ВСЕ'}")

    bucket_ms = int(a.window * 1000)
    densities = []
    raw_planks = []
    vis_max = {}
    exec_sum = defaultdict(float)
    streaks = {}
    prev_snap = {}
    tape = defaultdict(list)

    bar = Bar(ob_path.stat().st_size, "[стакан]")
    with open(ob_path, "rb") as fh:
        for raw in fh:
            bar.add(len(raw))
            try:
                d = json.loads(raw)
            except json.JSONDecodeError:
                continue
            sym = d.get("symbol")
            ts = d.get("timestamp")
            if not sym or ts is None:
                continue
            if allowed and sym not in allowed:
                continue
            snap = {}
            active = set()
            for side in ("bids", "asks"):
                rows = d.get(side, [])
                vols = [float(r[1]) for r in rows if len(r) == 2]
                prices = [pkey(r[0]) for r in rows if len(r) == 2]
                snap[side] = dict(zip(prices, vols))
                amin = abs_min(sym, a.abs_scale)
                for i, (pr, vol) in enumerate(zip(prices, vols)):
                    bkey = (sym, pr, ts // bucket_ms)
                    if vol > vis_max.get(bkey, 0):
                        vis_max[bkey] = vol
                    lo = max(0, i - a.density_win)
                    hi = min(len(vols), i + a.density_win + 1)
                    around = [vols[j] for j in range(lo, hi) if j != i]
                    avg = sum(around) / len(around) if around else 0.0
                    ratio = vol / avg if avg > 0 else 999.0
                    if vol >= amin and ratio >= a.density_ratio:
                        k = (sym, side, pr)
                        active.add(k)
                        st = streaks.get(k)
                        if st is None or ts - st[4] > 5000:
                            if st is not None:
                                densities.append(st)
                            streaks[k] = [sym, side, pr, ts, ts, vol, ratio, 1]
                        else:
                            st[4] = ts
                            st[5] = max(st[5], vol)
                            st[6] = max(st[6], ratio)
                            st[7] += 1
            for k in list(streaks.keys()):
                if k not in active and ts - streaks[k][4] > 5000:
                    densities.append(streaks.pop(k))
            ps = prev_snap.get(sym)
            if ps is not None:
                pts, pmap = ps
                dt = (ts - pts) / 1000.0
                if 0 < dt <= a.plank_max_dt:
                    amin = abs_min(sym, a.abs_scale)
                    for side in ("bids", "asks"):
                        old = pmap.get(side, {})
                        new = snap.get(side, {})
                        for pr in set(old) | set(new):
                            delta = new.get(pr, 0.0) - old.get(pr, 0.0)
                            if abs(delta) >= amin:
                                raw_planks.append((sym, side, pr, ts, delta))
            prev_snap[sym] = (ts, snap)
    bar.done()
    for st in streaks.values():
        densities.append(st)
    densities = [s for s in densities if (s[4] - s[3]) / 1000.0 >= a.density_hold]

    if have_trades:
        bar = Bar(tr_path.stat().st_size, "[лента]")
        with open(tr_path, "rb") as fh:
            for raw in fh:
                bar.add(len(raw))
                try:
                    d = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                sym = d.get("symbol") or d.get("ticker")
                ts = d.get("timestamp") or d.get("ts")
                pr = d.get("price")
                qty = d.get("qty") or d.get("amount") or 0
                if not sym or ts is None or pr is None:
                    continue
                if allowed and sym not in allowed:
                    continue
                pr = pkey(pr)
                qty = float(qty)
                exec_sum[(sym, pr, ts // bucket_ms)] += qty
                tape[sym].append((ts, pr, qty, d.get("side", "?")))
        bar.done()

    tss_by_sym = {}
    for sym in tape:
        tape[sym].sort(key=lambda x: x[0])
        tss_by_sym[sym] = [x[0] for x in tape[sym]]

    # ---- планки: агрегация в эпизоды ----
    bykey = defaultdict(list)
    for (sym, side, pr, ts, delta) in raw_planks:
        bykey[(sym, side, pr)].append((ts, delta))
    episodes = []
    for key, lst in bykey.items():
        lst.sort()
        cur = None
        for ts, delta in lst:
            if cur is not None and ts - cur["end"] <= EPISODE_GAP_MS:
                cur["end"] = ts
                cur["max"] = max(cur["max"], abs(delta))
                cur["n"] += 1
                if delta > 0:
                    cur["placed"] = True
                else:
                    cur["removed"] = True
            else:
                if cur is not None:
                    episodes.append(cur)
                cur = {"sym": key[0], "side": key[1], "pr": key[2],
                       "start": ts, "end": ts, "max": abs(delta), "n": 1,
                       "placed": delta > 0, "removed": delta < 0}
        if cur is not None:
            episodes.append(cur)
    episodes = [e for e in episodes if e["max"] >= abs_min(e["sym"], a.abs_scale)]
    episodes.sort(key=lambda e: -e["max"])

    # ---- айсберги ----
    icebergs = []
    bar = Bar(len(exec_sum), "[айсберг]")
    for k, ex in exec_sum.items():
        bar.add()
        if ex < a.iceberg_min_exec:
            continue
        vm = vis_max.get(k, 0)
        if vm <= 0:
            continue
        ratio = ex / vm
        if ratio >= a.iceberg_ratio:
            sym, pr, b = k
            icebergs.append((sym, pr, b * bucket_ms, ex, vm, ratio))
    bar.done()
    icebergs.sort(key=lambda x: -x[5])

    # ---- спред-роботы: эпизод поставил->снял + удар ----
    spreads = []
    bar = Bar(len(episodes), "[спред]")
    for ep in episodes:
        bar.add()
        if not (ep["placed"] and ep["removed"]):
            continue
        sym, pr = ep["sym"], ep["pr"]
        arr = tape.get(sym, [])
        tss = tss_by_sym.get(sym, [])
        i0 = bisect.bisect_left(tss, ep["end"])
        i1 = bisect.bisect_right(tss, ep["end"] + a.spread_gap)
        tol = max(0.02, pr * 0.001)
        hit = None
        for j in range(i0, i1):
            t2, p2, q2, s2 = arr[j]
            if abs(p2 - pr) <= tol and 0.5 <= q2 / ep["max"] <= 2.0:
                hit = (t2, p2, q2, s2)
                break
        if hit:
            spreads.append((sym, ep["side"], pr, ep["start"], ep["end"],
                            hit[0], ep["max"], hit[2], hit[3]))
    bar.done()
    spreads.sort(key=lambda x: -x[6])
    densities.sort(key=lambda s: -s[5])

    out = BASE / "output"
    out.mkdir(exist_ok=True)
    rep = out / f"market_structure_{date}.txt"
    L = []
    L.append("=" * 78)
    L.append(f" СКРИНЕР МИКРОСТРУКТУРЫ — {date}")
    L.append(f" стакан: {ob_path.name} | лента: {tr_path.name if have_trades else 'НЕТ'}")
    L.append(f" тикеры: {', '.join(sorted(allowed)) if allowed else 'ВСЕ'}")
    L.append("=" * 78)
    L.append("")
    L.append(f" ПЛОТНОСТИ: {len(densities)}")
    L.append("-" * 78)
    for s in densities[:a.top]:
        sym, side, pr, first, last, vol, ratio, n = s
        hold = (last - first) / 1000.0
        ru = "поддержка" if side == "bids" else "сопротивление"
        L.append(f"  {sym:10s} {ru:13s} {pr:<9} vol={vol:>9.0f} x{ratio:5.1f} держ={hold:5.0f}с "
                 f"{hhmmss(first)}-{hhmmss(last)}")
    if not densities:
        L.append("  (нет)")
    L.append("")
    L.append(f" АЙСБЕРГИ: {len(icebergs)}")
    L.append("-" * 78)
    for sym, pr, bts, ex, vm, ratio in icebergs[:a.top]:
        L.append(f"  {sym:10s} {pr:<9} исполнено={ex:>9.0f} видимо={vm:>8.0f} "
                 f"x{ratio:5.1f} окно {hhmmss(bts)}+{a.window:.0f}с")
    if not icebergs:
        L.append("  (нет)")
    L.append("")
    L.append(f" ПЛАНКИ (эпизоды): {len(episodes)}")
    L.append("-" * 78)
    for e in episodes[:a.top]:
        act = "поставлена+снята" if (e["placed"] and e["removed"]) else (
            "поставлена" if e["placed"] else "снята")
        L.append(f"  {e['sym']:10s} {e['side']:5s} {e['pr']:<9} {act:16s} "
                 f"max={e['max']:>9.0f} тиков={e['n']:>3} {hhmmss(e['start'])}-{hhmmss(e['end'])}")
    if not episodes:
        L.append("  (нет)")
    L.append("")
    L.append(f" СПРЕД-РОБОТЫ (эпизод поставил->снял->удар): {len(spreads)}")
    L.append("-" * 78)
    for sym, side, pr, t_pl, t_rm, t_hit, lot, qhit, sh in spreads[:a.top]:
        L.append(f"  {sym:10s} {side:5s} {pr:<9} лот={lot:>8.0f} "
                 f"выст {hhmmss(t_pl)} -> снял {hhmmss(t_rm)} -> удар {hhmmss(t_hit)} "
                 f"({t_hit - t_rm}мс) qty={qhit:.0f} {sh}")
    if not spreads:
        L.append("  (нет)")
    L.append("")
    L.append("=" * 78)
    L.append(f" параметры: density x{a.density_ratio} hold{a.density_hold:.0f}с | "
             f"iceberg x{a.iceberg_ratio} exec>={a.iceberg_min_exec:.0f} окно{a.window:.0f}с | "
             f"plank dt<={a.plank_max_dt}с эпизод gap<={EPISODE_GAP_MS}мс | spread gap<={a.spread_gap:.0f}мс")
    L.append("=" * 78)
    text = "\n".join(L)
    rep.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n[OK] отчёт: {rep}")


if __name__ == "__main__":
    main()