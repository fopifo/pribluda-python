"""
Приблуда на python — сверка 6-полевых строк (вероятно, сырые flags) с Алором.
Использует старые строки из quik_trades.csv (6 полей, 1025/1026 в конце).
"""
import json
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
QUIK_CSV = DATA_DIR / "quik_trades.csv"
DATE_STR = "2026-08-21"


def load_alor(tickers):
    """Загружает Алор для указанных тикеров за DATE_STR."""
    alor = {}
    for ticker in tickers:
        fpath = DATA_DIR / f"{ticker}_{DATE_STR}.json"
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


def parse_6field_line(line):
    """Парсит 6-полевую строку (с 1025/1026)."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = line.split(";")
    if len(parts) != 6:
        return None
    try:
        flags_val = int(parts[5])
        if flags_val not in (1025, 1026):
            return None
        return {
            "symbol": parts[0],
            "qty": float(parts[1]),
            "price": float(parts[2]),
            "side": parts[3],
            "timestamp": int(float(parts[4])),
            "flags": flags_val,
        }
    except (ValueError, IndexError):
        return None


def compare():
    print(f"=== Сверка 6-полевых строк с Алором за {DATE_STR} ===")

    # Читаем все тикеры из Алора
    alor_files = list(DATA_DIR.glob(f"*_{DATE_STR}.json"))
    alor_tickers = set(f.stem.split("_")[0] for f in alor_files)
    print(f"Тикеров Алора: {len(alor_tickers)}")

    alor_data = load_alor(alor_tickers)
    alor_index = build_alor_index(alor_data, tolerance_ms=500)
    print(f"Индекс Алора: {len(alor_index)} сделок")

    try:
        from datetime import datetime, timezone
        day_start = datetime.strptime(DATE_STR, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        day_start_ms = int(day_start.timestamp() * 1000)
        day_end_ms = day_start_ms + 24 * 60 * 60 * 1000
    except ValueError:
        print("Ошибка даты.")
        return

    print(f"Чтение 6-полевых строк из {QUIK_CSV.name}...")
    total_6field = 0
    matched = 0
    flags_match = 0
    side_match = 0

    with open(QUIK_CSV, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            tr = parse_6field_line(line)
            if not tr:
                continue
            ts = tr["timestamp"]
            if ts < day_start_ms or ts >= day_end_ms:
                continue
            total_6field += 1

            ticker = tr["symbol"]
            if ticker not in alor_tickers:
                continue

            bucket = round(ts / 500) * 500
            price = round(tr["price"], 4)
            qty = round(tr["qty"], 4)

            # Ищем в Алоре
            alor_side = None
            for off in (0, -500, 500):
                key = (ticker, bucket + off, price, qty)
                if key in alor_index:
                    alor_side = alor_index[key]
                    break

            if alor_side is None:
                continue

            matched += 1

            # Гипотеза: 1026 = buy, 1025 = sell
            flags_side = "buy" if tr["flags"] == 1026 else "sell"

            if flags_side == alor_side:
                flags_match += 1
            if tr["side"] == alor_side:
                side_match += 1

    print()
    print("=" * 60)
    print(f"РЕЗУЛЬТАТЫ ЗА {DATE_STR}")
    print("=" * 60)
    print(f"6-полевых строк за день: {total_6field}")
    print(f"Сопоставлено с Алором: {matched}")
    if matched > 0:
        pct_flags = flags_match / matched * 100
        pct_side = side_match / matched * 100
        print(f"flags (1025/1026) совпало: {flags_match} ({pct_flags:.1f}%)")
        print(f"side (tick-rule) совпало: {side_match} ({pct_side:.1f}%)")
        print()
        if pct_flags > pct_side:
            print(f"✓ flags (1025/1026) ТОЧНЕЕ на {pct_flags - pct_side:.1f}%")
            print("  → Использовать 6-е поле для определения стороны")
        elif pct_side > pct_flags:
            print(f"✓ tick-rule ТОЧНЕЕ на {pct_side - pct_flags:.1f}%")
            print("  → Оставить текущий tick-rule")
        else:
            print("= Оба метода одинаково точны")
    else:
        print("Не сопоставлено ни одной сделки.")
        print("Возможно, 6-полевых строк мало или даты не совпадают.")


if __name__ == "__main__":
    compare()