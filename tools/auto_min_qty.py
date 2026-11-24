"""
Понижает min_qty до лота робота там, где TW показывает узкую сетку (minLots>=5)
и наш min_qty выше неё. Только понижает. 1-лотовые не трогает (им нужен time-lock).
Запуск: python tools/auto_min_qty.py 2026-11-18
"""
import sys, json
from pathlib import Path
BASE=Path(__file__).resolve().parent.parent
def main():
    date=sys.argv[1] if len(sys.argv)>1 else "2026-11-18"
    tw=BASE/"data"/f"tw_robots_{date}.jsonl"
    st=BASE/"ticker_settings.json"
    data=json.loads(tw.read_text(encoding="utf-8"))
    robots=data.get("payload",{}).get("robots",[]) if isinstance(data,dict) else data
    settings=json.loads(st.read_text(encoding="utf-8"))
    minlot={}
    for r in robots:
        t=r.get("ticker"); ml=float(r.get("minLots",0))
        if t and ml>0:
            minlot[t]=min(minlot.get(t,1e9),ml)
    changed=[]
    for t,ml in minlot.items():
        if ml<5: continue
        cfg=settings.get(t)
        if not cfg: continue
        cur=cfg.get("min_qty",3)
        if cur>ml:
            cfg["min_qty"]=int(ml)
            changed.append((t,cur,int(ml)))
    st.write_text(json.dumps(settings,indent=2,ensure_ascii=False),encoding="utf-8")
    print(f"понижено min_qty для {len(changed)} тикеров:")
    for t,a,b in changed: print(f"  {t}: {a} -> {b}")
if __name__=="__main__":
    main()