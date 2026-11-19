"""
tools/positions_pnl.py — сверка PnL по позициям с терминалом/ЛК.
Читает data/bridge_state.json (avg_entry/pos_price, position, realized, best_bid/ask),
берёт MINSTEP и LOTSIZE из ISS MOEX, считает:
    пункты = (cur - avg) / MINSTEP * sign(pos)
    PnL_руб = пункты * |лот| * MINSTEP * LOTSIZE + realized
Только печатает, боевые файлы не трогает.
"""
import json
import urllib.request
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent.parent
BRIDGE = BASE / "data" / "bridge_state.json"

ISS = "https://iss.moex.com/iss/engines/{engine}/markets/{market}/boards/{board}/securities/{ticker}.json"
HDR = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def board_of(sym):
    s = sym.upper()
    if s == "CNYRUBF" or s.startswith("CNY-"):
        return "RFUD"
    if "TOM" in s or "TOD" in s:
        return "CETS"
    return "TQBR"


def iss_meta(ticker, board):
    engine = "futures" if board == "RFUD" else "stock"
    market = "forts" if board == "RFUD" else ("selt" if board == "CETS" else "shares")
    url = ISS.format(engine=engine, market=market, board=board, ticker=ticker)
    url += "?iss.only=securities,marketdata"
    url += "&securities.columns=SECID,MINSTEP,LOTSIZE"
    url += "&marketdata.columns=SECID,LAST"
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode())
    sec = data.get("securities", {})
    srows = sec.get("data") or []
    srow = dict(zip(sec.get("columns", []), srows[0])) if srows else {}
    md = data.get("marketdata", {})
    mrows = md.get("data") or []
    mrow = dict(zip(md.get("columns", []), mrows[0])) if mrows else {}
    return srow, mrow


def main():
    if not BRIDGE.exists():
        print("Нет data/bridge_state.json — запусти мост.")
        return
    state = json.loads(BRIDGE.read_text(encoding="utf-8"))
    syms = state.get("symbols", {})
    print("=" * 80)
    print(f" PnL ПО ПОЗИЦИЯМ (сверка с терминалом/ЛК)  {datetime.now():%H:%M:%S}")
    print("=" * 80)
    print(f"{'ТИКЕР':<12}{'ЛОТ':>6}{'СРЕД':>9}{'ТЕК':>9}{'ШАГ':>7}{'Ц/ШАГ':>8}{'ПНТ':>9}{'РУБ':>12}")
    print("-" * 80)
    total_rub = 0.0
    for sym, info in sorted(syms.items()):
        pos = info.get("position", 0) or 0
        if pos == 0 and abs(info.get("realized", 0) or 0) < 0.005:
            continue
        avg = info.get("avg_entry") or info.get("pos_price") or 0
        bb = info.get("best_bid", 0) or 0
        ba = info.get("best_ask", 0) or 0
        try:
            srow, mrow = iss_meta(sym, board_of(sym))
            cur = mrow.get("LAST")
            if cur is None and bb and ba:
                cur = (bb + ba) / 2
            step = srow.get("MINSTEP")
            lot = srow.get("LOTSIZE") or 1
            if not (avg and cur and step):
                print(f"{sym:<12}{pos:>6g}  -- нет avg/cur/шага --")
                continue
            cost = step * lot
            sign = 1 if pos > 0 else -1
            pts = (cur - avg) / step * sign
            rub = pts * abs(pos) * cost + (info.get("realized", 0) or 0)
            total_rub += rub
            print(f"{sym:<12}{pos:>6g}{avg:>9.3f}{cur:>9.3f}{step:>7.3f}{cost:>8.3f}{pts:>9.1f}{rub:>12.2f}")
        except Exception as e:
            print(f"{sym:<12}{pos:>6g}  ERR {e}")
    print("-" * 80)
    print(f"{'ИТОГО ₽':<12}{'':>6}{'':>9}{'':>9}{'':>7}{'':>8}{'':>9}{total_rub:>12.2f}")
    print("=" * 80)
    print("Ориентир ЛК (скрины): MTLR +141.14 | MTLRP -660 | CNYRUBF +428 | CNY-12.26 +21 | итого -518.86")


if __name__ == "__main__":
    main()