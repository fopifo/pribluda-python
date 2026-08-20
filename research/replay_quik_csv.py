"""
Приблуда на python — офлайн-реплей ленты Quik (data/quik_trades.csv)
через боевые детекторы. НИЧЕГО не меняет, только читает и считает.
Цель: увидеть, какие серии детектор находил бы из ЭТОГО файла в моменты
скриншотов (11:06:45 / 14:41:48 / 14:42:21 / 14:42:30 / 15:33:15),
и сравнить с живым окном и с окном конкурента.
ДОБАВЛЕНО: отладка первых строк CSV и прогресс.
"""
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from core.config import get_detector_configs
from core.ticker_settings import load_settings
from detectors.interval_robot import IntervalRobotDetector

CSV = BASE / "data" / "quik_trades.csv"
OUT = BASE / "output" / "replay_quik_csv.txt"
MSK = ZoneInfo("Europe/Moscow")
SNAPSHOTS = ["11:06:45", "14:41:48", "14:42:21", "14:42:30", "15:33:15"]

rep = []

def take_snapshot(dets, ts, label):
    rep.append("")
    rep.append(f"--- СНАПШОТ {label} (активные серии, repeats>=2) ---")
    rows = []
    for ds in dets.values():
        for d in ds:
            for r in d.get_active_snapshot(ts):
                if r["repeats"] >= 2:
                    rows.append(r)
    rows.sort(key=lambda r: (r["side"], r["symbol"], -r["repeats"]))
    if not rows:
        rep.append("  (пусто)")
    for r in rows:
        qty = "-".join(str(q) for q in r["qty_variants"])
        iv = f"{r['interval']:.0f}s" if r["interval"] else "-"
        rep.append(f"  {r['side']:<4} {r['symbol']:<7} qty={qty:<10} "
                   f"int={iv:<6} len={r['repeats']}")

def last_date_of_csv():
    with open(CSV, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 4096))
        tail = f.read().decode("utf-8", errors="ignore")
        lines = [l for l in tail.splitlines() if l.strip()]
        if not lines:
            return None
        p = lines[-1].split(";")
        if len(p) < 5:
            return None
        try:
            ts = int(float(p[4]))
        except ValueError:
            return None
        return datetime.fromtimestamp(ts / 1000, tz=MSK).date()

def main():
    if not CSV.exists():
        print(f"Файл не найден: {CSV}")
        return
    
    day = last_date_of_csv()
    if day is None:
        print("CSV пуст или не читается.")
        return
    
    print(f"Анализирую дату: {day}")
    snaps = []
    for hm_s in SNAPSHOTS:
        h, m, s = map(int, hm_s.split(":"))
        ts = datetime(day.year, day.month, day.day, h, m, s, tzinfo=MSK).timestamp()
        snaps.append([ts, hm_s, False])
    
    settings = load_settings()
    dets = {}
    
    def det_for(sym):
        if sym not in dets:
            ov = settings.get(sym, {})
            dets[sym] = [IntervalRobotDetector(sym, c)
                         for c in get_detector_configs(sym, ov.get("min_qty", 1), ov)]
        return dets[sym]
    
    rep.append("=" * 70)
    rep.append(f"РЕПЛЕЙ QUIK CSV за {day}")
    rep.append("=" * 70)
    
    # ОТЛАДКА: первые 5 строк CSV
    rep.append("")
    rep.append("-- ПЕРВЫЕ 5 СТРОК CSV --")
    with open(CSV, "r", encoding="utf-8", errors="ignore") as f:
        for i, line in enumerate(f):
            if i >= 5:
                break
            rep.append(f"  {i+1}: {line.strip()}")
    
    # ОТЛАДКА: первые 5 тикеров из settings
    rep.append("")
    rep.append("-- ПЕРВЫЕ 5 ТИКЕРОВ ИЗ SETTINGS --")
    for i, sym in enumerate(list(settings.keys())[:5]):
        rep.append(f"  {sym}")
    
    rep.append("")
    rep.append(f"Всего тикеров в settings: {len(settings)}")
    rep.append("")
    
    fed = 0
    lines = 0
    skipped_sym = 0
    skipped_date = 0
    skipped_qty = 0
    
    with open(CSV, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            p = line.strip().split(";")
            if len(p) < 5:
                continue
            lines += 1
            if lines % 200000 == 0:
                print(f"  прочитано строк: {lines}, скормлено: {fed}, "
                      f"пропущено (sym): {skipped_sym}, (дата): {skipped_date}, (qty): {skipped_qty}", 
                      flush=True)
            try:
                ts = int(float(p[4]))
            except ValueError:
                continue
            ts_sec = ts / 1000.0
            dt = datetime.fromtimestamp(ts_sec, tz=MSK)
            if dt.date() != day:
                skipped_date += 1
                continue
            
            for sn in snaps:
                if not sn[2] and ts_sec >= sn[0]:
                    take_snapshot(dets, sn[0], sn[1])
                    sn[2] = True
            
            sym = p[0]
            if sym not in settings:
                skipped_sym += 1
                continue
            try:
                qty = int(float(p[1]))
                price = float(p[2])
            except ValueError:
                continue
            
            ov = settings.get(sym, {})
            min_qty = ov.get("min_qty", 10)
            if qty < min_qty:
                skipped_qty += 1
                continue
            
            trade = {"symbol": sym, "qty": qty, "price": price,
                     "side": p[3], "timestamp": ts}
            for d in det_for(sym):
                d.on_trade(trade)
            fed += 1
    
    for sn in snaps:
        if not sn[2]:
            take_snapshot(dets, sn[0], sn[1])
            sn[2] = True
    
    rep.append("")
    rep.append(f"Итого: строк={lines}, скормлено детекторам={fed}, "
               f"тикеров с детекторами={len(dets)}")
    rep.append(f"Пропущено: sym={skipped_sym}, дата={skipped_date}, qty={skipped_qty}")
    
    report = "\n".join(rep)
    print(report)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(report, encoding="utf-8")
    print(f"\nОтчёт сохранён: {OUT}")

if __name__ == "__main__":
    main()