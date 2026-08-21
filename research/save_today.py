"""
Приблуда на python — скачать ПОЛНУЮ ленту сделок за СЕГОДНЯ через MOEX ISS
trades.json. ISS отдаёт максимум 5000 сделок за запрос, поэтому пагинация
идёт шагом 5000 через start, пока страница не станет неполной.
Кладёт data/{TICKER}_{сегодня}.json. Есть индикатор [i/75] + число страниц.
Запуск: python research/save_today.py
"""
import sys, json, time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from core.ticker_settings import load_settings

MSK = ZoneInfo("Europe/Moscow")
DATA = BASE / "data"
PAGE = 5000
ISS = ("https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR"
       "/securities/{sym}/trades.json")


def get_active_symbols(settings: dict) -> list[str]:
    return [sym for sym, cfg in settings.items() if cfg.get("active", True)]


def fetch_all(symbol):
    trades = []
    start = 0
    pages = 0
    while True:
        try:
            r = requests.get(ISS.format(sym=symbol),
                             params={"start": start, "limit": PAGE, "iss.meta": "off"},
                             timeout=30)
        except requests.RequestException:
            break
        if r.status_code != 200:
            break
        try:
            block = r.json().get("trades", {})
        except ValueError:
            break
        cols = block.get("columns", [])
        rows = block.get("data", [])
        if not cols or not rows:
            break
        idx = {c: i for i, c in enumerate(cols)}
        for row in rows:
            tdate = row[idx["TRADEDATE"]]
            ttime = row[idx["TRADETIME"]]
            try:
                ts = datetime.strptime(f"{tdate} {ttime}", "%Y-%m-%d %H:%M:%S") \
                        .replace(tzinfo=MSK).timestamp()
            except ValueError:
                continue
            bs = row[idx["BUYSELL"]]
            trades.append({
                "qty": row[idx["QUANTITY"]],
                "side": "buy" if bs == "B" else "sell",
                "timestamp": int(ts * 1000),
                "price": row[idx["PRICE"]],
            })
        pages += 1
        if len(rows) < PAGE:
            break
        start += len(rows)
        time.sleep(0.1)
    return trades, pages


def main():
    print("Скачиваю ПОЛНУЮ ленту за сегодня (пагинация по 5000)...", flush=True)
    today_str = datetime.now(MSK).strftime("%Y-%m-%d")
    settings = load_settings()
    symbols = get_active_symbols(settings)
    total = len(symbols)
    ok = 0
    for i, s in enumerate(symbols, 1):
        trades, pages = fetch_all(s)
        if trades:
            trades.sort(key=lambda t: t["timestamp"])
            (DATA / f"{s}_{today_str}.json").write_text(
                json.dumps(trades, ensure_ascii=False), encoding="utf-8")
            ok += 1
        print(f"[{i}/{total}] {s}: {len(trades)} сделок, {pages} стр.", flush=True)
        time.sleep(0.1)
    print(f"Готово: ok={ok}/{total}, дата={today_str}", flush=True)


if __name__ == "__main__":
    main()