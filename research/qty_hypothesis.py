"""
Гипотеза qty-vs-time + диагностика фона. ОБЯЗАТЕЛЬНЫЙ прогресс-бар.
Однопроходное чтение ленты. A=default(qty), B=ignore_qty+min1(time).
Печатает total сделок / прошедших min_qty, чтобы видеть долю фона.
Запуск: python research/qty_hypothesis.py 2026-11-18
"""
import sys, json, time, tempfile
from pathlib import Path
from collections import defaultdict
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from core.ticker_settings import load_settings
from core.config import get_detector_configs
from detectors.interval_robot import IntervalRobotDetector

TICKERS = ["GMKN","SIBN","TATN","LKOH","CNRU","X5","SNGSP","FLOT"]

class Progress:
    def __init__(self,total,label):
        self.total=max(int(total),1); self.label=label; self.done=0
        self.t0=time.time(); self.last=0.0; self.lp=-1; self.tty=sys.stderr.isatty()
    def _line(self):
        pct=min(self.done*100//self.total,100); dt=max(time.time()-self.t0,1e-9)
        sp=self.done/dt; eta=(self.total-self.done)/max(sp,1)
        bar="#"*(pct//5)+"."*(20-pct//5)
        return f"[qty_hyp] {self.label} [{bar}] {pct:3d}% | {self.done/1e6:.0f}/{self.total/1e6:.0f}MB | {sp/1e6:.1f}MB/s | ETA {eta:.0f}s"
    def update(self,n):
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
    date=sys.argv[1] if len(sys.argv)>1 else "2026-11-18"
    settings=load_settings()
    fpath=BASE/"data"/f"live_trades_{date}.jsonl"
    total_size=fpath.stat().st_size
    dets={}; counts=defaultdict(lambda:[0,0])
    for sym in TICKERS:
        ov=dict(settings.get(sym,{}))
        for tag,ig,mq in (("A",False,ov.get("min_qty",3)),("B",True,1)):
            ds=[]
            for c in get_detector_configs(sym,mq,ov):
                c=dict(c); c["ignore_qty"]=ig; c["min_qty"]=mq
                d=IntervalRobotDetector(sym,c); d._history_path=Path(tempfile.mktemp(suffix=".jsonl")); ds.append(d)
            dets[(sym,tag)]=ds
    p=Progress(total_size,"feed")
    with open(fpath,encoding="utf-8") as f:
        for line in f:
            p.update(len(line)); line=line.strip()
            if not line: continue
            t=json.loads(line); sym=t.get("symbol")
            if sym not in TICKERS: continue
            side=t.get("side"); qty=int(float(t.get("qty",0)))
            counts[(sym,side)][0]+=1
            if qty>=settings.get(sym,{}).get("min_qty",3): counts[(sym,side)][1]+=1
            tr={"symbol":sym,"qty":qty,"price":float(t.get("price",0)),"side":side,"timestamp":int(t.get("timestamp",0))}
            for tag in ("A","B"):
                for d in dets[(sym,tag)]: d.on_trade(tr)
    p.close()
    for sym in TICKERS:
        print(f"\n=== {sym} ===")
        for side in ("buy","sell"):
            tot,passed=counts[(sym,side)]
            if tot: print(f"  {side}: всего={tot} прошло_min_qty={passed} фон={tot-passed}")
        for tag in ("A","B"):
            sig=[]
            for d in dets[(sym,tag)]:
                sig+= [(s.side,round(s.interval_avg,1),s.repeats) for s in d.flush()]
            print(f"  {tag}: {len(sig)} сигналов {sorted(sig,key=lambda x:-x[2])[:5]}")

if __name__=="__main__":
    main()