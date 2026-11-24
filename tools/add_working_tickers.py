"""
tools/add_working_tickers.py — добавляет РАБОЧИЕ тикеры владельца в
ticker_settings.json (идемпотентно: существующие не трогает).
Нужно, чтобы все инструменты со скрина попали в роботов-детектор.
Юань/фьючи/валюта получают min_qty=1 (мелкие лоты), акции — дефолт.
Запуск: python tools/add_working_tickers.py
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
P = BASE / "ticker_settings.json"

# Рабочий набор владельца (скрин терминала). Существующие пропускаются.
WORKING = [
    # акции (верх/низ скрина)
    "RUAL", "MDMG", "MTLR", "FIXR", "MTLRP", "CHMF", "LENT", "NLMK",
    "UPRO", "HEAD", "ASTR", "ALRS", "ENPG", "BANE", "SIBN", "BELU",
    "ROSN", "MOEX", "PHOR", "DOMRF", "SNGSP", "AFKS", "CNRU", "SVCB",
    "PIKK", "GMKN", "RENI", "AFLT", "X5", "IRAO", "BSPB", "MTSS",
    "ETLN", "NVTK", "MVID", "FEES", "DATA", "TRNFP", "OGKB", "POSI",
    "SMLT", "WUSH", "MSNG", "IVAT", "MAGN", "RTKM", "MGNT", "FLOT",
    "TRMK", "HYDR", "LSRG", "TATNP", "LKOH",
    # юань / фьючи / валюта (мост CScalp)
    "CNYRUBF", "CNY-12.26", "CNYRUB_TOM", "CNY000000TOD",
]
FUT_CUR = {"CNYRUBF", "CNY-12.26", "CNYRUB_TOM", "CNY000000TOD"}


def main():
    data = json.loads(P.read_text(encoding="utf-8-sig"))
    added = []
    for sym in WORKING:
        if sym in data:
            continue
        mq = 1 if sym in FUT_CUR else 10
        data[sym] = {
            "active": True,
            "min_qty": mq,
            "min_interval": 2.0,
            "max_interval": 600.0,
            "interval_tolerance": 0.05,
            "max_qty_ratio": 1.1,
            "min_display_repeats": 3,
            "min_double_hit_gap_sec": 0.0,
        }
        added.append(sym)
    P.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[add_working] добавлено {len(added)}: {', '.join(added) if added else '(ничего, все уже есть)'}")
    print(f"[add_working] всего тикеров в settings: {len(data)}")


if __name__ == "__main__":
    main()