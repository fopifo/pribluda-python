"""
риблуда на python — точечная настройка min_qty для SNGSP и SNGS.
Снижаем порог с 50 до 20, чтобы детектор видел длинных роботов с qty=27
(интервалы 11с и 22с, серии по 150-170 ударов), но не захлёбывался
в мусоре qty=10-15.
апуск: python tools/tune_sngsp.py
"""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SETTINGS = BASE / "ticker_settings.json"

def main():
    if not SETTINGS.exists():
        print(f"айл не найден: {SETTINGS}")
        return
    
    data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    changed = []
    
    for sym in ("SNGSP", "SNGS"):
        if sym in data:
            old_qty = data[sym].get("min_qty")
            if old_qty != 20:
                data[sym]["min_qty"] = 20
                changed.append(f"{sym}: min_qty {old_qty} -> 20")
                
    if changed:
        SETTINGS.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print("отово! несены изменения:")
        for c in changed:
            print(f"  - {c}")
    else:
        print("астройки уже актуальны.")

if __name__ == "__main__":
    main()