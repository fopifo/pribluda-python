"""
Приблуда на python — синхронизатор КОТИРОВОК из MOEX ISS (v2).
v2: пишет ТОЛЬКО data/quik_quotes.csv. Планки (quik_limits.csv) теперь
пишет lua (медленный опрос Quik), т.к. ISS планки по акциям не отдаёт
(подтверждено зондами и списком колонок 2026-08-20). TRADINGSTATUS из ISS
не пишем (коды статусов у ISS другие — не вводим вкладки в заблуждение);
статусы аукционов вкладка берёт по расписанию.
Колонки читаются ДИНАМИЧЕСКИ (используются только найденные).
Запуск в ОТДЕЛЬНОЙ консоли:
python tools/iss_quotes_sync.py
"""
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
QUOTES_CSV = BASE_DIR / "data" / "quik_quotes.csv"
QUOTES_TMP = BASE_DIR / "data" / "quik_quotes.tmp"

URL = ("https://iss.moex.com/iss/engines/stock/markets/shares/boards/"
       "TQBR/securities.json?iss.meta=off")
PERIOD = 5.0


def num(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def fmt(v):
    x = num(v)
    return "" if x is None else f"{x:g}"


def block_to_rows(block):
    cols = (block or {}).get("columns") or []
    idx = {c: i for i, c in enumerate(cols)}
    rows = []
    for row in (block or {}).get("data") or []:
        rows.append({c: (row[i] if i < len(row) else None) for c, i in idx.items()})
    return rows, idx


def main():
    printed_cols = False
    while True:
        try:
            r = requests.get(URL, timeout=15)
            r.raise_for_status()
            data = r.json()
            md, md_idx = block_to_rows(data.get("marketdata"))
            if not printed_cols:
                have = [c for c in ("LAST", "BID", "OFFER", "VOLTODAY")
                        if c in md_idx]
                print("marketdata key columns present:", have)
                printed_cols = True

            ts = int(time.time() * 1000)
            q = ["class;ticker;last;bid;offer;voltoday;valtoday;numtrades;"
                 "openperiod;tradingstatus;ts"]
            for rrow in md:
                t = rrow.get("SECID")
                if not t:
                    continue
                q.append("TQBR;" + t
                         + ";" + fmt(rrow.get("LAST"))
                         + ";" + fmt(rrow.get("BID"))
                         + ";" + fmt(rrow.get("OFFER"))
                         + ";" + fmt(rrow.get("VOLTODAY"))
                         + ";" + fmt(rrow.get("VALTODAY"))
                         + ";" + fmt(rrow.get("NUMTRADES"))
                         + ";;"          # openperiod (нет в ISS)
                         + ""            # tradingstatus (коды ISS другие)
                         + ";" + str(ts))

            QUOTES_TMP.write_text("\n".join(q) + "\n", encoding="utf-8")
            QUOTES_TMP.replace(QUOTES_CSV)
            print(f"{time.strftime('%H:%M:%S')} quotes={len(q)-1}")
        except Exception as e:
            print(f"ISS error: {type(e).__name__}: {e}")
        time.sleep(PERIOD)


if __name__ == "__main__":
    main()