"""
reverse_engineer.py — дата-дривен реверс-инжиниринг эталона (v2).
FIX: зазоры/regularity считаем по ДОМИНИРУЮЩЕМУ qty-кластеру (свои сделки робота),
а не по всему окну с фоном — иначе reg/gap_med были мусором (0.000).
Positive = окна TW-роботов; Negative = наши FP + случайные окна без роботов.
Отчёт: output/reverse_engineer_<date>.txt
Запуск: python research/reverse_engineer.py 2026-11-18
"""
import sys, json, time, random
import numpy as np
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
MSK = ZoneInfo("Europe/Moscow")
OUT = BASE / "output"
FEATS = ["n", "gap_med", "reg", "top1", "top2", "q_ratio", "dur", "tpm", "p_range"]


class Progress:
    def __init__(self, total, label):
        self.total = max(int(total), 1); self.label = label; self.done = 0
        self.t0 = time.time(); self.last = 0.0; self.lp = -1; self.tty = sys.stderr.isatty()
    def _line(self):
        pct = min(self.done * 100 // self.total, 100); dt = max(time.time() - self.t0, 1e-9)
        sp = self.done / dt; eta = (self.total - self.done) / max(sp, 1)
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        return f"[reveng] {self.label} [{bar}] {pct:3d}% | {self.done}/{self.total} | {sp:.0f}/s | ETA {eta:.0f}s"
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


def median(x):
    return float(np.median(x)) if len(x) else 0.0


def feats(stream, t0, t1):
    pts = [(ts, q, p) for (ts, q, p) in stream if t0 <= ts <= t1]
    n = len(pts)
    if n < 8:
        return None
    qs = [x[1] for x in pts]; ps = [x[2] for x in pts]
    # доминирующий qty-кластер = собственные сделки робота
    qc = Counter(qs)
    q0 = qc.most_common(1)[0][0]
    cluster = {q for q in qc if q0 * 0.9 <= q <= q0 * 1.1}
    cpts = [x for x in pts if x[1] in cluster]
    if len(cpts) < 6:
        cpts = pts
    cts = sorted(x[0] for x in cpts)
    gaps = [cts[i+1] - cts[i] for i in range(len(cts) - 1)]
    gmed = median(gaps)
    tol = max(0.5, 0.2 * gmed)
    reg = (sum(1 for g in gaps if abs(g - round(g / gmed) * gmed) <= tol) / len(gaps)) if gmed > 0 else 0.0
    vals = sorted(qc.values(), reverse=True)
    top1 = vals[0] / n
    top2 = sum(vals[:2]) / n
    cq = sorted(cluster)
    qratio = (cq[-1] / max(cq[0], 1)) if cq else 1.0
    dur = pts[-1][0] - pts[0][0] if n > 1 else 0.0
    tpm = n / (dur / 60.0) if dur > 0 else 0.0
    pmed = median(ps)
    prange = (max(ps) - min(ps)) / pmed if pmed > 0 else 0.0
    return dict(n=n, gap_med=gmed, reg=reg, top1=top1, top2=top2,
                q_ratio=qratio, dur=dur, tpm=tpm, p_range=prange)


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else "2026-11-18"
    tape = BASE / "data" / f"live_trades_{date}.jsonl"
    twp = BASE / "data" / f"tw_robots_{date}.jsonl"
    ourp = BASE / "data" / "replay_alor_history.jsonl"
    for p in (tape, twp, ourp):
        if not p.exists():
            print(f"[!] нет {p.name}"); return

    groups = defaultdict(list)
    size = tape.stat().st_size
    pr = Progress(size, "read_tape")
    with open(tape, encoding="utf-8") as f:
        for line in f:
            pr.update(len(line)); line = line.strip()
            if not line: continue
            t = json.loads(line)
            sym = t.get("symbol"); side = (t.get("side") or "").lower()
            if not sym or side not in ("buy", "sell"): continue
            groups[(sym, side)].append((int(t.get("timestamp", 0)) / 1000.0,
                                        int(float(t.get("qty", 0))),
                                        float(t.get("price", 0))))
    pr.close()
    for k in groups: groups[k].sort(key=lambda x: x[0])

    tw = json.loads(twp.read_text(encoding="utf-8"))
    robots = tw.get("payload", {}).get("robots", []) if isinstance(tw, dict) else tw
    robots = [r for r in robots if 2.0 <= float(r.get("interval", 0)) / 1000.0 <= 60.0]
    scope = {r["ticker"] for r in robots}
    rob_by_key = defaultdict(list)
    for r in robots:
        ls = datetime.fromisoformat(r["start"].replace("Z", "+00:00")).timestamp()
        le = datetime.fromisoformat(r["end"].replace("Z", "+00:00")).timestamp()
        rob_by_key[(r["ticker"], "buy" if r.get("isBuy") else "sell")].append((ls, le, r))

    ours = [json.loads(l) for l in ourp.read_text(encoding="utf-8").splitlines() if l.strip()]
    ours = [o for o in ours if 0 < float(o.get("interval_avg", 0)) <= 60.0 and o.get("symbol") in scope]

    rows = []
    for key, lst in rob_by_key.items():
        stream = groups.get(key)
        if not stream: continue
        for ls, le, r in lst:
            f = feats(stream, ls, le)
            if f: rows.append((1, f))
    for o in ours:
        key = (o["symbol"], o["side"].lower())
        st = o.get("start_ts") or (o.get("start_ms") or 0) / 1000.0
        en = o.get("end_ts") or st + float(o.get("repeats", 1)) * float(o.get("interval_avg", 0))
        iv = float(o["interval_avg"]); qset = set(o.get("qty_variants", []))
        adm = False
        for ls, le, r in rob_by_key.get(key, []):
            per = float(r["interval"]) / 1000.0
            if abs(iv - per) > max(2.0, per * 0.2): continue
            lo, hi = float(r.get("minLots", 0)), float(r.get("maxLots", 0))
            if not any(lo <= q <= hi for q in qset): continue
            if min(en, le) - max(st, ls) > 0: adm = True; break
        if not adm:
            stream = groups.get(key)
            if stream:
                f = feats(stream, st, en)
                if f: rows.append((0, f))
    rnd = random.Random(42)
    for key, stream in groups.items():
        if key[0] not in scope or not stream: continue
        t0, t1 = stream[0][0], stream[-1][0]
        added = 0
        for _ in range(6):
            if t1 - t0 < 700: break
            s = rnd.uniform(t0, t1 - 600); e = s + 600
            if any(min(e, le) - max(s, ls) > 0 for ls, le, _ in rob_by_key.get(key, [])): continue
            f = feats(stream, s, e)
            if f: rows.append((0, f)); added += 1
            if added >= 3: break

    X = np.array([[f[k] for k in FEATS] for _, f in rows], dtype=float)
    y = np.array([l for l, _ in rows], dtype=float)
    npos, nneg = int(y.sum()), int(len(y) - y.sum())
    lines = [f"=== REVERSE ENGINEER v2 {date} ===", f"positive={npos} negative={nneg}", ""]
    lines.append(f"{'feature':10s} {'med_POS':>10s} {'med_NEG':>10s} {'best_thr_acc':>12s}")
    for j, k in enumerate(FEATS):
        v = X[:, j]
        mp = median(v[y == 1]); mn = median(v[y == 0])
        ths = np.quantile(v, np.linspace(0.05, 0.95, 40))
        best = max(max(((v > t) == (y == 1)).mean(), ((v <= t) == (y == 1)).mean()) for t in ths)
        lines.append(f"{k:10s} {mp:10.3f} {mn:10.3f} {best:12.3f}")
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd
    w = np.zeros(Z.shape[1]); b = 0.0
    for _ in range(400):
        z = np.clip(Z @ w + b, -30, 30)
        p = 1.0 / (1.0 + np.exp(-z))
        w -= 0.1 * (Z.T @ (p - y) / len(y)); b -= 0.1 * (p - y).mean()
    acc = (((Z @ w + b) > 0).astype(float) == y).mean()
    lines += ["", f"LR accuracy={acc:.3f}", "веса (|w| убыв.):"]
    lines += [f"  {k:10s} {wi:+.3f}" for k, wi in sorted(zip(FEATS, w), key=lambda x: -abs(x[1]))]
    txt = "\n".join(lines)
    OUT.mkdir(exist_ok=True)
    (OUT / f"reverse_engineer_{date}.txt").write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()