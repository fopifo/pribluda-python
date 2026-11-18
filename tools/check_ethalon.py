"""
tools/check_ethalon.py — проверка, пишутся ли роботы ЭТАЛОНОВ (TW + Aniscan).
Короткий, без длинных однострочников (лечит PSReadLine).
"""
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
MSK = ZoneInfo("Europe/Moscow")
TODAY = datetime.now(MSK).date().isoformat()
now = time.time()


def report(fname, label):
    p = os.path.join(DATA, fname)
    if not os.path.exists(p):
        print(f"  ❌ {label:<16} ФАЙЛ НЕ СОЗДАН ({fname})")
        return
    age = now - os.path.getmtime(p)
    size = os.path.getsize(p) / 1024
    n = 0
    ids = set()
    try:
        with open(p, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                n += 1
                try:
                    r = json.loads(line)
                    rid = r.get("id")
                    if rid is not None:
                        ids.add(rid)
                except Exception:
                    pass
    except Exception as e:
        print(f"  ❌ {label:<16} не читается: {e}")
        return
    mark = "✅" if age <= 120 else "⚠️"
    extra = f" | уник.id={len(ids)}" if ids else ""
    print(f"  {mark} {label:<16} возраст {age:>6.1f}с | {size:>8.1f}КБ | строк={n}{extra}")


print("=" * 66)
print(f" ПРОВЕРКА ЭТАЛОНОВ | {TODAY} | МСК {datetime.now(MSK).strftime('%H:%M:%S')}")
print("=" * 66)
print("\n[эталон T-Widgets]")
report(f"tw_robots_{TODAY}.jsonl", "tw_robots")
print("\n[эталон Aniscan]")
report(f"aniscan_robots_{TODAY}.jsonl", "aniscan_robots")
print("\n[разведка live-API Aniscan]")
report("aniscan_debug.jsonl", "aniscan_debug")
print("\n" + "=" * 66)
print("Подсказка: если aniscan_robots нет/старый — открой в Chrome")
print("https://aniscan.ru/robot-history/table и обнови (F5).")
print("=" * 66)