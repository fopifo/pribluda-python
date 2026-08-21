"""
Приблуда на python — накопление ЭТАЛОНА (роботы конкурента) в
data/competitor_history.jsonl. Источники (сливаются, дедупликация):
1. research/competitor_robots_*.csv      — база (ничего не теряем);
2. research/competitor_supplement_*.jsonl— дополнения, которые отдаёт чат;
3. уже имеющийся data/competitor_history.jsonl — сохраняется.
v2: колонка времени "start" распознана (было только "time") — timestamp
больше не None, статистика по дням/часам работает.
Запуск: python tools/import_competitor_csv.py  (идемпотентно, можно поверх)
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
    "time": ["start", "time", "время"],          # v2: добавлен "start"
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


def _key(r):
    q = (r.get("qty_variants") or [None])[0]
    return (r.get("symbol"), r.get("side"), r.get("timestamp"), q)


def load_existing():
    recs, seen = [], set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            k = _key(r)
            if k not in seen:
                seen.add(k)
                recs.append(r)
    return recs, seen


def add_csv(src, recs, seen):
    date_from_name = None
    for part in src.stem.split("_"):
        if len(part) == 10 and part[4] == "-":
            date_from_name = part
    with open(src, encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return 0
        m = map_header(header)
        if "symbol" not in m:
            print(f"Шапка не распознана в {src.name}: {header} — пропуск.")
            return 0
        added = 0
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
                try:
                    rec["qty_variants"] = [int(float(row[m["qty"]]))]
                except ValueError:
                    pass
            if "interval" in m:
                try:
                    rec["interval_avg"] = float(row[m["interval"]].rstrip("sс"))
                except ValueError:
                    pass
            ts = None
            if "time" in m and date_from_name:
                try:
                    ts = datetime.fromisoformat(
                        f"{date_from_name} {row[m['time']]}").replace(tzinfo=MSK)
                except ValueError:
                    ts = None
            if ts:
                rec["timestamp"] = ts.isoformat(timespec="seconds")
            k = _key(rec)
            if k not in seen:
                seen.add(k)
                recs.append(rec)
                added += 1
        return added


def add_supplement(src, recs, seen):
    added = 0
    for line in src.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        k = _key(r)
        if k not in seen:
            seen.add(k)
            recs.append(r)
            added += 1
    return added


def main():
    recs, seen = load_existing()
    base_n = len(recs)

    a_csv = sum(add_csv(s, recs, seen)
               for s in sorted(BASE.glob("research/competitor_robots_*.csv")))
    a_sup = sum(add_supplement(s, recs, seen)
                for s in sorted(BASE.glob("research/competitor_supplement_*.jsonl")))

    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"База={base_n}, +CSV={a_csv}, +дополнения={a_sup}, итого={len(recs)} -> {OUT}")


if __name__ == "__main__":
    main()