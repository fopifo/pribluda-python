"""
Приблуда на python — подстройка настроек детектора против шума.
v4: добавлен SMALL_QTY — точечные пороги для тикеров, где конкурент
видит роботов с малым объёмом (4-20 лотов). Без этого мы их не видим.
- interval_tolerance: 10% -> 5% (только если не настроено)
- min_repeats: 3 -> 4
- min_qty: 10 -> 20 (глобально), 50 (для спамеров), SMALL_QTY (приоритет)
- max_qty_ratio: None -> 1.10
- min_display_repeats: 3 (кандидаты в UI с 3 повторов)
Создаёт резервную копию ticker_settings.json.bak
"""
import json
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SETTINGS = BASE / "ticker_settings.json"

# Тикеры-спамеры, которые генерируют тонны мусора с мелкими сделками
SPAMMERS = {
    "ASTR", "AFKS", "SNGSP", "ALRS", "CHMF", "IVAT", "SVCB", "BELU",
    "LKOH", "OZON", "PLZL", "MTLR", "GMKN", "RUAL", "T", "ROSN",
    "MAGN", "NLMK", "YDEX", "PIKK", "PHOR", "VTBR", "SBER", "GAZP",
    "NVTK", "MTSS", "RTKM", "RTKMP", "SNGS", "TATNP", "TRNFP", "MTLRP",
    "IRAO", "FEES", "HEAD", "DOMRF", "FLOT", "RAGR", "RENI", "SMLT",
    "WUSH", "X5", "AFLT", "BANEP", "BSPB", "CBOM", "DATA", "ELMT",
    "ETLN", "FIXR", "GLRX", "LENT", "MBNK", "MDMG", "MGNT", "MOEX",
    "MSNG", "MVID", "NMTP", "OGKB", "POSI", "SIBN", "TRMK", "UPRO",
    "VKCO",
}

# v4: точечные пороги по наблюдениям за конкурентом (приоритет над SPAMMERS)
SMALL_QTY = {
    "RENI": 4,    # конкурент видит 4-5
    "BSPB": 10,   # конкурент видит 10-30
    "GMKN": 10,   # конкурент видит 10-11 (buy)
    "CNRU": 10,   # конкурент видит 10-11
    "NLMK": 9,    # конкурент видит 9-10
    "FEES": 19,   # конкурент видит 19
    "HEAD": 19,   # конкурент видит 19-20
    "DOMRF": 23,  # конкурент видит 23-24
    "SNGS": 29,   # конкурент видит 29-30
    "BELU": 16,   # конкурент видит 16-17
    "LENT": 20,   # конкурент видит 20
    "CHMF": 33,   # конкурент видит 33-57
    "PIKK": 40,   # конкурент видит 40-41
    "MAGN": 35,   # конкурент видит 35
    "POSI": 44,   # конкурент видит 44-45
    "MGNT": 42,   # конкурент видит 42-43
    "X5": 25,     # конкурент видит 25-144
    "UGLD": 43,   # конкурент видит 43-54
    "TATN": 20,   # конкурент видит 20-1405
}

def main():
    if not SETTINGS.exists():
        print(f"Файл не найден: {SETTINGS}")
        return
    data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    shutil.copy2(SETTINGS, BASE / "ticker_settings.json.bak")
    print(f"Резервная копия: ticker_settings.json.bak")
    changed = 0
    for sym, s in data.items():
        old = dict(s)
        s["min_repeats"] = 4
        s["max_qty_ratio"] = 1.10
        if "interval_tolerance" not in s:
            s["interval_tolerance"] = 0.05
        s["min_display_repeats"] = 3
        
        # Приоритет: SMALL_QTY -> SPAMMERS -> дефолт 20
        if sym in SMALL_QTY:
            s["min_qty"] = SMALL_QTY[sym]
        elif sym in SPAMMERS:
            s["min_qty"] = 50
        else:
            if s.get("min_qty", 10) < 20:
                s["min_qty"] = 20
                
        if s != old:
            changed += 1
    SETTINGS.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Готово: изменено {changed} из {len(data)} тикеров.")

if __name__ == "__main__":
    main()