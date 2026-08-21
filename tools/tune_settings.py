"""
Приблуда на python — подстройка настроек детектора против шума.
- interval_tolerance: 10% -> 5% (только если не настроено)
- min_repeats: 3 -> 4
- min_qty: 10 -> 20 (глобально), 50 (для спамеров),
  но MIN_QTY_OVERRIDES имеет приоритет (тикеры с мелкими роботами конкурента)
- max_qty_ratio: None -> 1.10
- min_display_repeats: 3 (кандидаты в UI с 3 повторов)
Создаёт резервную копию ticker_settings.json.bak
ПЕРЕНОС: из корня в tools/ (архитектура).
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

# v4: точечные min_qty по эталону (приоритет над SPAMMERS и дефолтом).
# Значение = минимальный qty робота конкурента (скрины 10:00-11:11
# и research/competitor_robots_2026-08-20.csv).
MIN_QTY_OVERRIDES = {
    "RENI": 4,    # конкурент: RENI 4-5 @16s
    "BSPB": 10,   # конкурент: BSPB 10-34 @37-74s
    "LENT": 15,   # конкурент: LENT 19-21 @16s
    "CNRU": 10,   # конкурент: CNRU 10-11 @10s
    "PHOR": 9,    # конкурент: PHOR 9-10 @12s
    "PIKK": 40,   # конкурент: PIKK 40-41 @12s
    "DOMRF": 20,  # конкурент: DOMRF 23-24 @12s
    "HEAD": 19,   # конкурент: HEAD 19-20 @12s
    "SNGS": 25,   # конкурент: SNGS 29-30 @13s
    "GMKN": 10,   # конкурент: GMKN buy 10-11 @16s
    "TATNP": 50,  # конкурент: TATNP 50-51 @12s (явно, для ясности)
    "FLOT": 50,   # конкурент: FLOT 87-88 @12s (явно, для ясности)
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
        # min_repeats: всегда 4
        s["min_repeats"] = 4
        # max_qty_ratio: всегда 1.10
        s["max_qty_ratio"] = 1.10
        # interval_tolerance: только если не настроено
        if "interval_tolerance" not in s:
            s["interval_tolerance"] = 0.05
        # min_display_repeats: 3 (кандидаты в UI с 3 повторов)
        s["min_display_repeats"] = 3
        # min_qty: приоритет у точечных значений, затем спамеры/дефолт
        if sym in MIN_QTY_OVERRIDES:
            s["min_qty"] = MIN_QTY_OVERRIDES[sym]
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