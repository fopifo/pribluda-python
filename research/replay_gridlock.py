"""
Оффлайн-прогон GridLock (лот-агностичный) со СКОЛЬЗЯЩИМИ ОКНАМИ.
window_sec=600 (10 мин), step=300 (5 мин). Пишет data/replay_gridlock_history.jsonl.
Post-filter: для каждого (ticker,side) оставляет только top-3 сетки по repeats.
НЕ трогает боевые файлы/GUI. Прогресс-бар обязателен.
Запуск: python research/replay_gridlock.py 2026-11-18
Сравнение: python research/tw_compare.py 2026-11-18 --ours data/replay_gridlock_history.jsonl
"""
import sys, json, time
from pathlib import Path
from collections import defaultdict
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from detectors.gridlock_detector import detect

HIST = BASE / "data" / "replay_gridlock_history.jsonl"
WINDOW_SEC = 600
STEP_SEC = 300
TOP_K_PER_TICKER_SIDE = 3

class Progress:
    def __init__(self, total, label):
        self.total=max(int(total),1); self.label=label; self.done=0
        self.t0=time.time(); self.last=0.0; self.lp=-1; self.tty=sys.stderr.isatty()
    def _line(self):
        pct=min(self.done*100//self.total,100); dt=max(time.time()-self.t0,1e-9)
        sp=self.done/dt; eta=(self.total-self.done)/max(sp,1)
        bar="#"*(pct//5)+"."*(20-pct//5)
        return f"[gridlock] {self.label} [{bar}] {pct:3d}% | {self.done}/{self.total} | {sp:.0f}/s | ETA {eta:.0f}s"
    def update(self,n=1):
        self.done+=n; pct=min(self.done*100//self.total,100); now=time.time()
        if self.tty:
            if now-self.last<0.25 and self.done<self.total: return
            self.last=now; sys.stderr.write("\r"+self._line()); sys.stderr.flush()
        else:
            if pct//10!=self.lp//10: self.lp=pct; print(self._line(),file=sys.stderr,flush=True)
    def close(self):
        if self.tty: sys.stderr.write("\r"+self._line()+"\n")
        else: print(self._line(),file=sys.stderr,flush=True)

def main():
    date = sys.argv[1] if len(sys.argv)>1 else "2026-11-18"
    fpath = BASE/"data"/f"live_trades_{date}.jsonl"
    if not fpath.exists():
        print(f"[gridlock] нет {fpath.name}"); return
    groups = defaultdict(lambda: [[],[]])
    size = fpath.stat().st_size
    p = Progress(size, "read")
    with open(fpath, encoding="utf-8") as f:
        for line in f:
            p.update(len(line)); line=line.strip()
            if not line: continue
            t=json.loads(line)
            sym=t.get("symbol"); side=t.get("side")
            if not sym or not side: continue
            g=groups[(sym,side)]
            g[0].append(int(t.get("timestamp",0))/1000.0)
            g[1].append(int(float(t.get("qty",0))))
    p.close()
    if HIST.exists(): HIST.unlink()
    
    total_windows = 0
    for (sym,side),(ts,qs) in groups.items():
        if not ts: continue
        ts_sorted = sorted(ts)
        t0, t1 = ts_sorted[0], ts_sorted[-1]
        start = t0
        while start <= t1:
            total_windows += 1
            start += STEP_SEC
    
    p2 = Progress(total_windows, "detect_windows")
    all_recs = []
    
    for (sym,side),(ts,qs) in groups.items():
        if not ts: continue
        pairs = sorted(zip(ts, qs), key=lambda x: x[0])
        t0, t1 = pairs[0][0], pairs[-1][0]
        start = t0
        while start <= t1:
            p2.update()
            end = start + WINDOW_SEC
            w_ts = [t for t, q in pairs if start <= t < end]
            w_qs = [q for t, q in pairs if start <= t < end]
            if len(w_ts) >= 15:
                for r in detect(w_ts, w_qs):
                    r["symbol"] = sym
                    r["side"] = side
                    r["preset"] = "gridlock"
                    all_recs.append(r)
            start += STEP_SEC
    p2.close()
    
    # Post-filter: для каждого (ticker,side) оставляем только top-3 по repeats
    by_key = defaultdict(list)
    for r in all_recs:
        by_key[(r["symbol"], r["side"])].append(r)
    
    final = []
    for key, recs in by_key.items():
        recs.sort(key=lambda x: -x["repeats"])
        final.extend(recs[:TOP_K_PER_TICKER_SIDE])
    
    with open(HIST,"a",encoding="utf-8") as out:
        for r in final:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
    
    print(f"[gridlock] окон={total_windows} сырых={len(all_recs)} после_top3={len(final)} -> {HIST}", file=sys.stderr)

if __name__=="__main__":
    main()