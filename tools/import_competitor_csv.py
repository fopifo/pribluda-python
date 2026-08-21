"""
Приблуда на python — импорт роботов конкурента из research/competitor_robots_*.csv
в data/competitor_history.jsonl (формат вкладки "Статистика").
Автоопределение колонок по синонимам. Если шапка не распознана — печатает
её и выходит, НЕ перезаписывая jsonl (безопасно).
Запуск: python tools/import_competitor_csv.py
"""
import csv, json, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
MSK = ZoneInfo("Europe/Moscow")
OUT = BASE / "data" / "competitor_history.jsonl"

ALIASES = {
    "symbol": ["ticker", "symbol", "тикер", "sec"],
    "side": ["side", "сторона", "dir", "напр"],
    "qty": ["qty", "quantity", "объем", "volume", "кол"],
    "interval": ["int", "interval", "int_sec", "интервал"],
    "time": ["time", "время"],
    "date": ["date", "дата"],
}


def map_header(header):
    low = [h.strip().lower() for h in header]
    m = {}
    for field, syns in ALIASES.items():
        for i, h in enumerate(low):
            if h in syns:
                m[field] = i
                break
    return m


def main():
    srcs = sorted(BASE.glob("research/competitor_robots_*.csv"))
    if not srcs:
        print("Не найдено research/competitor_robots_*.csv")
        return
    records = []
    for src in srcs:
        date_from_name = None
        for part in src.stem.split("_"):
            if len(part) == 10 and part[4] == "-":
                date_from_name = part
        with open(src, encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                continue
            m = map_header(header)
            if "symbol" not in m:
                print(f"Шапка не распознана в {src.name}: {header}")
                print("Импорт пропущен (jsonl не тронут).")
                continue
            for row in reader:
                if not row or len(row) <= max(m.values()):
                    continue
                sym = row[m["symbol"]].strip()
                if not sym:
                    continue
                rec = {"symbol": sym,
                       "side": row[m["side"]].strip() if "side" in m else "?",
                       "timestamp": None, "interval_avg": None}
                if "qty" in m:
                    try: rec["qty_variants"] = [int(float(row[m["qty"]]))]
                    except ValueError: pass
                if "interval" in m:
                    try: rec["interval_avg"] = float(row[m["interval"]].rstrip("sс"))
                    except ValueError: pass
                ts = None
                if "date" in m and "time" in m:
                    try:
                        ts = datetime.fromisoformat(f"{row[m['date']]} {row[m['time']]}").replace(tzinfo=MSK)
                    except ValueError: ts = None
                elif "time" in m and date_from_name:
                    try:
                        ts = datetime.fromisoformat(f"{date_from_name} {row[m['time']]}").replace(tzinfo=MSK)
                    except ValueError: ts = None
                rec["timestamp"] = ts.isoformat(timespec="seconds") if ts else None
                records.append(rec)
    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Готово: {len(records)} записей -> {OUT}")


if __name__ == "__main__":
    main()