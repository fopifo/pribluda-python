"""
Приблуда на python — сверка сторон сделок между нашим quik_trades.csv и Алором.
Сравнивает нашу сторону (из CSV) с истинной стороной Алора за один день.
Использование: python research/compare_sides.py ГГГГ-ММ-ДД
"""
import json
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
QUIK_CSV = DATA_DIR / "quik_trades.csv"

TIME_TOLERANCE_MS = 500  # допуск по времени для сопоставления


def load_alor_data(date_str):
    """Загружает все файлы Алора за указанную дату: {тикер: [сделки]}."""
    files = list(DATA_DIR.glob(f"*_{date_str}.json"))
    if not files:
        print(f"Файлы Алора за {date_str} не найдены.")
        return {}
    alor = {}
    for f in files:
        ticker = f.stem.split("_")[0]
        try:
            with open(f, "r", encoding="utf-8") as fp:
                trades = json.load(fp)
            alor[ticker] = trades
        except (json.JSONDecodeError, OSError) as e:
            print(f"  Ошибка чтения {f.name}: {e}")
    print(f"Загружено тикеров Алора: {len(alor)}")
    return alor


def build_alor_index(alor_data):
    """Индекс сделок Алора: (тикер, время_бакет, цена, объём) -> сторона."""
    index = {}
    for ticker, trades in alor_data.items():
        for t in trades:
            ts = t.get("timestamp")
            price = t.get("price")
            qty = t.get("qty")
            side = t.get("side")
            if ts is None or price is None or qty is None or side is None:
                continue
            bucket = round(ts / TIME_TOLERANCE_MS) * TIME_TOLERANCE_MS
            key = (ticker, bucket, round(price, 4), round(qty, 4))
            index[key] = side
    print(f"Индекс Алора: {len(index)} сделок")
    return index


def parse_quik_line(line):
    """Парсит строку quik_trades.csv (5 или 6 полей — берём первые 5)."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = line.split(";")
    if len(parts) < 5:
        return None
    try:
        return {
            "symbol": parts[0],
            "qty": float(parts[1]),
            "price": float(parts[2]),
            "side": parts[3],
            "timestamp": int(float(parts[4])),
        }
    except (ValueError, IndexError):
        return None


def compare(date_str):
    print(f"=== Сверка сторон за {date_str} ===")
    alor_data = load_alor_data(date_str)
    if not alor_data:
        return
    alor_tickers = set(alor_data.keys())
    alor_index = build_alor_index(alor_data)

    try:
        day_start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        day_start_ms = int(day_start.timestamp() * 1000)
        day_end_ms = day_start_ms + 24 * 60 * 60 * 1000
    except ValueError:
        print("Неверный формат даты. Нужно ГГГГ-ММ-ДД.")
        return

    print(f"Чтение {QUIK_CSV.name} (фильтр по дате)...")
    total = 0
    matched = 0
    side_match = 0
    side_mismatch = 0
    stats = defaultdict(lambda: {"matched": 0, "match": 0, "mismatch": 0})

    with open(QUIK_CSV, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            tr = parse_quik_line(line)
            if not tr:
                continue
            ts = tr["timestamp"]
            if ts < day_start_ms or ts >= day_end_ms:
                continue
            total += 1
            ticker = tr["symbol"]
            if ticker not in alor_tickers:
                continue

            bucket = round(ts / TIME_TOLERANCE_MS) * TIME_TOLERANCE_MS
            price = round(tr["price"], 4)
            qty = round(tr["qty"], 4)
            found = None
            for off in (0, -TIME_TOLERANCE_MS, TIME_TOLERANCE_MS):
                key = (ticker, bucket + off, price, qty)
                if key in alor_index:
                    found = alor_index[key]
                    break
            if found is None:
                continue

            matched += 1
            stats[ticker]["matched"] += 1
            if tr["side"] == found:
                side_match += 1
                stats[ticker]["match"] += 1
            else:
                side_mismatch += 1
                stats[ticker]["mismatch"] += 1

    print()
    print("=" * 60)
    print(f"РЕЗУЛЬТАТЫ ЗА {date_str}")
    print("=" * 60)
    print(f"Сделок в quik_trades.csv за день: {total}")
    print(f"Сопоставлено с Алором: {matched}")
    if matched > 0:
        pct = side_match / matched * 100
        print(f"Совпадение сторон: {side_match} ({pct:.1f}%)")
        print(f"Расхождение сторон: {side_mismatch} ({100 - pct:.1f}%)")
        print()
        print("Топ-10 тикеров по сопоставлениям:")
        top = sorted(stats.items(), key=lambda x: x[1]["matched"], reverse=True)[:10]
        for tk, s in top:
            if s["matched"] == 0:
                continue
            p = s["match"] / s["matched"] * 100
            print(f"  {tk}: {s['matched']} сделок, {s['match']} совпало ({p:.1f}%)")
    else:
        print("Не сопоставлено ни одной сделки.")
        print("Проверьте дату и наличие данных в обоих источниках.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python research/compare_sides.py ГГГГ-ММ-ДД")
        print("Пример: python research/compare_sides.py 2026-08-21")
        sys.exit(1)
    compare(sys.argv[1])