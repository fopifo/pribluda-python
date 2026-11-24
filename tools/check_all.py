"""
tools/check_all.py — быстрая проверка, что все процессы живы и пишут.
Ожидание < 2 сек, прогресс-бар не нужен.
"""
import json
import os
import time
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
MSK = ZoneInfo("Europe/Moscow")
TODAY = datetime.now(MSK).date().isoformat()
now = time.time()


def age_of(fname):
    """Возраст файла в секундах или None если нет."""
    p = os.path.join(DATA, fname)
    if not os.path.exists(p):
        return None
    return round(now - os.path.getmtime(p), 1)


def size_kb(fname):
    p = os.path.join(DATA, fname)
    if not os.path.exists(p):
        return 0.0
    return round(os.path.getsize(p) / 1024, 1)


def check_feed(fname, label, max_age=5.0):
    a = age_of(fname)
    if a is None:
        print(f"  ❌ {label:<22} ФАЙЛ НЕ СОЗДАН ({fname})")
        return False
    ok = a <= max_age
    mark = "✅" if ok else "⚠️"
    print(f"  {mark} {label:<22} возраст {a:>6.1f}с | {size_kb(fname):>8.1f} КБ")
    return ok


print("=" * 70)
print(f" ПРОВЕРКА ЗАПИСИ  |  дата {TODAY}  |  время МСК {datetime.now(MSK).strftime('%H:%M:%S')}")
print("=" * 70)

# 1. Живые потоки моста
print("\n[1] ПОТОКИ МОСТА (лента/стакан):")
check_feed(f"pbsdk_trades_{TODAY}.jsonl", "лента сделок", 5.0)
check_feed(f"orderbook_{TODAY}.jsonl", "стакан", 5.0)

# 2. bridge_state (позиции)
print("\n[2] bridge_state.json (позиции моста):")
a = age_of("bridge_state.json")
if a is None:
    print("  ❌ bridge_state.json НЕ СОЗДАН")
else:
    print(f"  {'✅' if a <= 3 else '⚠️'} возраст {a:.1f}с")
    try:
        st = json.load(open(os.path.join(DATA, "bridge_state.json"), encoding="utf-8"))
        for k, v in st.get("symbols", {}).items():
            if v.get("position", 0) != 0:
                print(f"      {k:<12} pos={v.get('position')}")
    except Exception as e:
        print(f"      ❌ не читается: {e}")

# 3. Фандинг
print("\n[3] funding (фандинг вечников):")
a = age_of(f"funding_{TODAY}.json")
if a is None:
    print(f"  ❌ funding_{TODAY}.json НЕ СОЗДАН")
else:
    print(f"  ✅ возраст {a:.1f}с")
    try:
        fd = json.load(open(os.path.join(DATA, f"funding_{TODAY}.json"), encoding="utf-8"))
        n = len(fd.get("funding", {}))
        my = fd.get("my_positions", {})
        print(f"      тикеров: {n} | мои позиции: {len(my)}")
        for t, mp in my.items():
            print(f"      {t}: поз {mp.get('position')} → {mp.get('total_funding'):+.2f} ₽")
    except Exception as e:
        print(f"      ❌ не читается: {e}")

# 4. Снапшоты позиций
print("\n[4] Снапшоты позиций (positions_*.json):")
snaps = sorted([x for x in os.listdir(DATA) if x.startswith("positions_")])
if not snaps:
    print("  ❌ снапшотов нет")
else:
    for x in snaps[-3:]:
        print(f"  ✅ {x}  возраст {age_of(x):.1f}с")

# 5. TW-эталон
print("\n[5] T-Widgets (tw_robots):")
a = age_of(f"tw_robots_{TODAY}.jsonl")
if a is None:
    print(f"  ⚠️ tw_robots_{TODAY}.jsonl нет (TW не активен / браузер закрыт?)")
else:
    print(f"  {'✅' if a <= 120 else '⚠️'} возраст {a:.1f}с | {size_kb(f'tw_robots_{TODAY}.jsonl'):.1f} КБ")

# 6. Команды бота
print("\n[6] trade_commands.jsonl (бот пишет заявки):")
a = age_of("trade_commands.jsonl")
if a is None:
    print("  ⚠️ файла нет (бот ещё не торговал)")
else:
    print(f"  ✅ возраст {a:.1f}с | {size_kb('trade_commands.jsonl'):.1f} КБ")

# 7. Процессы
print("\n[7] ПРОЦЕССЫ:")
for img, exp in [("python.exe", "≥5 (tw_server,snapshot,main,bot,чекер)"), ("dotnet.exe", "1 (мост)")]:
    try:
        out = subprocess.check_output(["tasklist", "/FI", "IMAGENAME eq " + img]).decode("cp866", "ignore")
        cnt = out.count(img)
        print(f"  {img:<12} запущено: {cnt}   (ожидаем {exp})")
    except Exception as e:
        print(f"  {img:<12} ошибка: {e}")

print("\n" + "=" * 70)
print(" Готово. ❌ = критично, ⚠️ = настороже, ✅ = ок.")
print("=" * 70)