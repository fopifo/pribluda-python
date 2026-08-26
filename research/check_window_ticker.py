"""
Приблуда на python — проверка сырых сделок тикера в окне времени.
Печатает сделки с объёмом, стороной и интервалом до предыдущей сделки,
чтобы глазами увидеть, есть ли робот конкурента в нашей ленте и
не рвёт ли его сторона (tick-rule/flags).
Запуск: python research/check_window_ticker.py ТИКЕР ДАТА ЧЧ:ММ ЧЧ:ММ
"""
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
CSV = BASE / "data" / "quik_trades.csv"
MSK = ZoneInfo("Europe/Moscow")

def main():
    if len(sys.argv) < 5:
        print("Использование: python research/check_window_ticker.py ТИКЕР ДАТА ЧЧ:ММ ЧЧ:ММ")
        return
    sym, date, a, b = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    lo = datetime.strptime(f"{date} {a}", "%Y-%m-%d %H:%M").replace(tzinfo=MSK).timestamp() * 1000
    hi = datetime.strptime(f"{date} {b}", "%Y-%m-%d %H:%M").replace(tzinfo=MSK).timestamp() * 1000
    prev_ts = None
    n = 0
    with open(CSV, encoding="utf-8", errors="ignore") as f:
        for line in f:
            p = line.strip().split(";")
            if len(p) < 5 or p[0] != sym:
                continue
            try:
                ts = int(float(p[4]))
            except ValueError:
                continue
            gap = f"gap={(ts - prev_ts) / 1000:.1f}s" if prev_ts else ""
            prev_ts = ts
            if ts < lo or ts >= hi:
                continue
            print(f"{datetime.fromtimestamp(ts / 1000, tz=MSK):%H:%M:%S} "
                  f"qty={p[1]:>5} side={p[3]:<4} {gap}")
            n += 1
    print(f"Всего сделок {sym} в окне {a}-{b}: {n}")

if __name__ == "__main__":
    main()