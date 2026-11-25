# -*- coding: utf-8 -*-
# Веб-панель сущностей v8 final: отсчёт CD относительный (не зависит от рассинхрона
# часов), заглушка "серий нет" корректно убирается, без мигания, данные раз в секунду.
# Покупки слева, продажи справа, сортировка по LEN.
import json, os, socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = r"C:\Users\Public\MI_CODES\dpNEW"
DATA = os.path.join(ROOT, "data")
PORT = 8788

def load_json(name):
    try:
        with open(os.path.join(DATA, name), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None

PAGE = """<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Entities - dpNEW</title>
<style>
 :root { color-scheme: dark; }
 body { background:#0d1117; color:#c9d1d9; font:14px/1.45 Consolas,monospace; margin:0; padding:10px; }
 h1 { font-size:15px; margin:0 0 8px; color:#58a6ff; }
 #bal { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:8px 12px;
        margin-bottom:10px; display:flex; gap:16px; flex-wrap:wrap; font-size:13px; }
 #bal b { color:#e6edf3; }
 #wrap { display:flex; gap:12px; align-items:flex-start; flex-wrap:wrap; }
 .col { flex:1 1 460px; min-width:340px; }
 .cap { font-size:13px; font-weight:bold; padding:4px 8px; border-radius:6px 6px 0 0; }
 .capB { background:#12261a; color:#3fb950; border:1px solid #1f6feb33; border-bottom:none; }
 .capS { background:#2a1214; color:#f85149; border:1px solid #f8514933; border-bottom:none; }
 table { border-collapse:collapse; width:100%; table-layout:fixed; }
 th { color:#8b949e; text-align:left; border-bottom:1px solid #30363d; padding:4px 6px; white-space:nowrap; overflow:hidden; }
 td { padding:4px 6px; border-bottom:1px solid #21262d; white-space:nowrap; overflow:hidden; }
 .jms-g { color:#3fb950; font-weight:bold; } .jms-y { color:#d29922; font-weight:bold; }
 .jms-r { color:#f85149; font-weight:bold; }
 .tag { background:#1f6feb33; color:#58a6ff; border:1px solid #1f6feb66; border-radius:4px;
        padding:0 5px; font-size:12px; }
 .sym { color:#e6edf3; font-weight:bold; }
 .cd-alarm { color:#f85149; font-weight:bold; }
 #upd { color:#8b949e; font-size:12px; margin-top:8px; }
</style></head><body>
<h1>СУЩНОСТИ - серии ударов роботов</h1>
<div id="bal">
 <span>Баланс: <b id="b_total">-</b></span><span>Свободно: <b id="b_free">-</b></span>
 <span>Заблок.: <b id="b_blocked">-</b></span><span>ПнЛ: <b id="b_pnl">-</b></span>
 <span>Комиссия: <b id="b_comm">-</b></span>
</div>
<div id="wrap">
 <div class="col"><div class="cap capB">ПОКУПКИ (B)</div>
  <table><thead><tr><th>CD</th><th>TICKER</th><th>QTY</th><th>INT</th><th>NEXT</th>
  <th>JMS</th><th>LPP</th><th>VPM</th><th>LEN</th><th>TAGS</th></tr></thead>
  <tbody id="tbB"></tbody></table></div>
 <div class="col"><div class="cap capS">ПРОДАЖИ (S)</div>
  <table><thead><tr><th>CD</th><th>TICKER</th><th>QTY</th><th>INT</th><th>NEXT</th>
  <th>JMS</th><th>LPP</th><th>VPM</th><th>LEN</th><th>TAGS</th></tr></thead>
  <tbody id="tbS"></tbody></table></div>
</div>
<div id="upd"></div>
<script>
const ROWMAP = {B:new Map(), S:new Map()};   // cluster_id -> {tr, cells, cd0, t0}
function jmsCls(v){ return v<100 ? 'jms-g' : (v<=300 ? 'jms-y' : 'jms-r'); }
function fmt(n,d){ if(n==null) return '-'; return Number(n).toLocaleString('ru-RU',
  {maximumFractionDigits:d==null?2:d}); }
function makeRow(){
  const tr=document.createElement('tr');
  const cells=[];
  for(let i=0;i<10;i++){ const td=document.createElement('td'); cells.push(td); tr.appendChild(td); }
  cells[0].className='cd';
  cells[1].className='sym';
  const r={tr, cells, cd0:0, t0:0};
  tr.__row=r;
  return r;
}
function updateRow(r,c,base){
  r.cd0=Math.max(0,(c.next_ms-base)/1000);
  r.t0=performance.now();
  r.cells[1].textContent=c.symbol;
  const q=(c.lot_med!=null)?c.lot_med:c.avg_lot;
  r.cells[2].textContent=fmt(q,0);
  r.cells[3].textContent=c.int_s.toFixed(1);
  r.cells[4].textContent=c.int_s.toFixed(1);
  const j=r.cells[5];
  j.textContent=c.jms_ms.toFixed(0);
  const cls=jmsCls(c.jms_ms);
  if(j.className!==cls) j.className=cls;
  r.cells[6].textContent=c.lpp;
  r.cells[7].textContent=fmt(c.vpm,1);
  r.cells[8].textContent=c.n;
  r.cells[9].innerHTML=(c.tags||[]).map(t=>'<span class="tag">'+t+'</span>').join('');
}
function renderSide(side, tbId){
  const tb=document.getElementById(tbId);
  const map=ROWMAP[side];
  const rows=(side==='B') ? window.__B : window.__S;
  const list=rows.slice().sort((a,b)=>b.n-a.n);
  const seen=new Set();
  const base=window.__updms||0;
  for(const c of list){
    const key=c.cluster_id;
    seen.add(key);
    let r=map.get(key);
    if(!r){ r=makeRow(); map.set(key,r); }
    updateRow(r,c,base);
    tb.appendChild(r.tr);
  }
  for(const [key,r] of Array.from(map)){
    if(!seen.has(key)){ r.tr.remove(); map.delete(key); }
  }
  const ph=tb.querySelectorAll('tr.ph');
  if(list.length){ ph.forEach(p=>p.remove()); }
  if(!list.length && !tb.children.length){
    const tr=document.createElement('tr'); tr.className='ph';
    const td=document.createElement('td');
    td.colSpan=10; td.style.color='#8b949e'; td.textContent='серий нет';
    tr.appendChild(td); tb.appendChild(tr);
  }
}
async function pull(){
  try{
    const r=await fetch('/api/live?t='+Date.now());
    const j=await r.json();
    window.__updms=j.updated_ms||0;
    const all=j.clusters||[];
    window.__B=all.filter(c=>c.side==='buy');
    window.__S=all.filter(c=>c.side!=='buy');
    renderSide('B','tbB');
    renderSide('S','tbS');
    document.getElementById('upd').textContent =
      'обновлено '+new Date(j.updated_ms).toLocaleTimeString('ru-RU');
    if(j.balance){
      const b=j.balance;
      const set=(id,v)=>{ if(v!=null) document.getElementById(id).textContent=v; };
      set('b_total',b.TotalAssets); set('b_free',b.FreeAssets);
      set('b_blocked',b.BlockedAssets); set('b_pnl',b.PnL); set('b_comm',b.Commission);
    }
  }catch(e){
    document.getElementById('upd').textContent='нет связи с агрегатором';
  }
}
function tickCd(){
  const t=performance.now();
  document.querySelectorAll('td.cd').forEach(td=>{
    const r=td.parentNode.__row;
    if(!r) return;
    const cd=r.cd0-(t-r.t0)/1000;
    td.textContent=(cd<=0? '0' : cd.toFixed(0));
    td.classList.toggle('cd-alarm', cd<=0);
  });
}
pull(); setInterval(pull,1000); setInterval(tickCd,200);
</script></body></html>"""

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/live"):
            j = load_json("entities_live.json") or {"updated_ms": 0, "clusters": []}
            st = load_json("bridge_state.json")
            if st:
                j["balance"] = st.get("balance") or st
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(j, ensure_ascii=False).encode("utf-8"))
        elif self.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
        else:
            self._send(404, "text/plain", b"not found")

    def log_message(self, *a):
        pass

def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()

if __name__ == "__main__":
    ip = lan_ip()
    print("Панель v8: http://127.0.0.1:%d  (телефон: http://%s:%d)" % (PORT, ip, PORT))
    print("Ctrl+C - стоп.")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
