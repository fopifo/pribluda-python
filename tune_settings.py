"""
Приблуда на python — подстройка настроек детектора против шума.
Допуск интервала 10% -> 5%, подтверждение с 4 повторов, min_qty не ниже 10.
Создаёт резервную копию ticker_settings.json.bak
"""
import json
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent
SETTINGS = BASE / "ticker_settings.json"

def main():
    data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    shutil.copy2(SETTINGS, BASE / "ticker_settings.json.bak")

    changed = 0
    for sym, s in data.items():
        old = dict(s)
        s["interval_tolerance"] = 0.05   # строже: 5% вместо 10%
        s["min_repeats"] = 4             # подтверждение с 4 повторов
        if s.get("min_qty", 10) < 10:    # отсечь мелочь типа GMKN 1-25
            s["min_qty"] = 10
        if s != old:
            changed += 1

    SETTINGS.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Готово: изменено {changed} из {len(data)} тикеров.")
    print("Резервная копия: ticker_settings.json.bak")

if __name__ == "__main__":
    main()