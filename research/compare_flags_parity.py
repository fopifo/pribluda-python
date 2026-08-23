"""
Приблуда на python — прямая сверка гипотезы flags%2 с истинной стороной Алора.
Использует данные зонда probe_datasource_ticks.lua (data/probe_datasource.csv)
и сравнивает flags_parity с эталоном Алора за тот же период.
"""
import json
import csv
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
PROBE_CSV = DATA_DIR / "probe_datasource.csv"


def load_alor_for_tickers(tickers, date_str="2026-08-23"):
    """Загружает Алор для указанных тикеров за указанную дату."""
    alor = {}
    for ticker in tickers:
        fpath = DATA_DIR / f"{ticker}_{date_str}.json"
        if not fpath.exists():
            continue
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                alor[ticker] = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
    return alor


def build_alor_index(alor_data, tolerance_ms=500):
    """Индекс: (тикер, время_бакет, цена, объём) -> сторона."""
    index = {}
    for ticker, trades in alor_data.items():
        for t in trades:
            ts = t.get("timestamp")
            price = t.get("price")
            qty = t.get("qty")
            side = t.get("side")
            if None in (ts, price, qty, side):
                continue
            bucket = round(ts / tolerance_ms) * tolerance_ms
            key = (ticker, bucket, round(price, 4), round(qty, 4))
            index[key] = side
    return index


def compare():
    if not PROBE_CSV.exists():
        print(f"Файл {PROBE_CSV} не найден. Запусти зонд probe_datasource_ticks.lua.")
        return

    # Читаем тикеры из зонда
    tickers_in_probe = set()
    probe_trades = []
    with open(PROBE_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tickers_in_probe.add(row["ticker"])
            probe_trades.append(row)

    print(f"Тикеры в зонде: {sorted(tickers_in_probe)}")
    print(f"Всего сделок в зонде: {len(probe_trades)}")

    # Определяем дату по первому timestamp
    if not probe_trades:
        print("Нет данных для анализа.")
        return

    first_ts_ms = int(probe_trades[0]["timestamp_ms"])
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(first_ts_ms / 1000.0, tz=timezone.utc)
    date_str = dt.strftime("%Y-%m-%d")
    print(f"Дата зонда: {date_str}")

    # Загружаем Алор
    alor_data = load_alor_for_tickers(tickers_in_probe, date_str)
    if not alor_data:
        print(f"Алор за {date_str} не найден для тикеров зонда.")
        print("Попробую загрузить за 2026-08-21 (пятница)...")
        alor_data = load_alor_for_tickers(tickers_in_probe, "2026-08-21")
        if not alor_data:
            print("Алор не найден.")
            return

    alor_index = build_alor_index(alor_data, tolerance_ms=500)
    print(f"Индекс Алора: {len(alor_index)} сделок")

    # Сравниваем
    matched = 0
    flags_match = 0
    ot_match = 0

    for row in probe_trades:
        ticker = row["ticker"]
        ts_ms = int(row["timestamp_ms"])
        price = float(row["price"])
        qty = float(row["qty"])
        flags_parity = row["flags_parity"]
        side_ot = row["side_onalltrade"]

        bucket = round(ts_ms / 500) * 500
        key = (ticker, bucket, round(price, 4), round(qty, 4))
        
        # Ищем в Алоре с допуском по времени
        alor_side = None
        for off in (0, -500, 500):
            k = (ticker, bucket + off, round(price, 4), round(qty, 4))
            if k in alor_index:
                alor_side = alor_index[k]
                break

        if alor_side is None:
            continue

        matched += 1
        if flags_parity == alor_side:
            flags_match += 1
        if side_ot == alor_side:
            ot_match += 1

    print()
    print("=" * 60)
    print(f"СРАВНЕНИЕ: flags%2 vs OnAllTrade (прямая сверка с Алором)")
    print("=" * 60)
    print(f"Сопоставлено сделок: {matched}")
    if matched > 0:
        pct_flags = flags_match / matched * 100
        pct_ot = ot_match / matched * 100
        print(f"flags%2 совпало с Алором: {flags_match} ({pct_flags:.1f}%)")
        print(f"OnAllTrade совпало с Алором: {ot_match} ({pct_ot:.1f}%)")
        print()
        if pct_flags > pct_ot:
            print(f"✓ flags%2 ТОЧНЕЕ на {pct_flags - pct_ot:.1f}%")
            print("  → Заменить tick-rule на flags%2 в export_trades.lua")
        elif pct_ot > pct_flags:
            print(f"✓ OnAllTrade ТОЧНЕЕ на {pct_ot - pct_flags:.1f}%")
            print("  → Оставить текущий tick-rule")
        else:
            print("= Оба метода одинаково точны")
    else:
        print("Не сопоставлено ни одной сделки.")


if __name__ == "__main__":
    compare()