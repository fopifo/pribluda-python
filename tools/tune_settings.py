"""
Приблуда на python — подстройка настроек детектора против шума.
- interval_tolerance: 10% -> 5% (только если не настроено)
- min_repeats: 3 -> 4
- min_qty: 10 -> 20 (глобально), 50 (для спамеров)
- max_qty_ratio: None -> 1.10
- min_display_repeats: 2 -> 3 (v3: пары LEN2 = шум, не показываем)
Создаёт резервную копию ticker_settings.json.bak
ПЕРЕНОС: из корня в tools/ (архитектура).
"""
import json
import shutil
from pathlib import Path
BASE = Path(__file__).resolve().parent.parent
SETTINGS = BASE / "ticker_settings.json"

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
        s["min_display_repeats"] = 3   # v3: пары скрыты
        if sym in SPAMMERS:
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