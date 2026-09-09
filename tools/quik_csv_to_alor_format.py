"""
Приблуда на python — конвертер Quik CSV -> формат Алор JSON.

ФАЙЛ:
    tools/quik_csv_to_alor_format.py

Назначение:
    Берёт data/quik_trades.csv и создаёт файлы:
        data/<TICKER>_YYYY-MM-DD.json

    Формат совместим с research/compare_aniscan_grid.py:
        [
          {
            "id": 0,
            "symbol": "SBER",
            "qty": 10.0,
            "price": 300.12,
            "time": "2026-09-09T10:15:12.123000+03:00",
            "timestamp": 1788931578864,
            "side": "buy"
          },
          ...
        ]

Почему нужен:
    Алор не отдаёт историю за сегодняшний день:
        "From and To must be less than today"
    Поэтому для 09-09 используем живую ленту Quik.

Использование:
    python tools/quik_csv_to_alor_format.py 2026-09-09

v1.1 (2026-09-09):
    - добавлен прогресс-бар по байтам файла;
    - потоковая запись JSON-массивов по тикерам, без хранения всего 1GB CSV в памяти;
    - безопасная перезапись через *.tmp -> *.json;
    - статистика тикеров/сделок на выходе.
"""

import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

MSK = timezone(timedelta(hours=3))
BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"
CSV_PATH = DATA_DIR / "quik_trades.csv"


class Progress:
    def __init__(self, total_bytes: int):
        self.total = max(total_bytes, 1)
        self.last_print = 0.0
        self.start = time.time()

    def show(self, done_bytes: int, trades: int, tickers: int, force: bool = False):
        now = time.time()
        if not force and now - self.last_print < 0.25:
            return
        self.last_print = now

        ratio = min(max(done_bytes / self.total, 0.0), 1.0)
        width = 20
        filled = int(width * ratio)
        bar = "#" * filled + "." * (width - filled)
        pct = int(ratio * 100)

        elapsed = max(now - self.start, 0.001)
        mb_done = done_bytes / 1024 / 1024
        mb_total = self.total / 1024 / 1024
        speed = mb_done / elapsed
        eta = int((mb_total - mb_done) / speed) if speed > 0 else 0

        print(
            f"\r[quik->json] [{bar}] {pct:3d}% | "
            f"{mb_done:.1f}/{mb_total:.1f} MB | "
            f"сделок={trades} тикеров={tickers} | "
            f"{speed:.1f} MB/s | ETA {eta}с",
            end="",
            flush=True,
        )


def _safe_float(x):
    try:
        return float(x)
    except Exception:
        return None


def _safe_int_ms(x):
    try:
        return int(float(x))
    except Exception:
        return None


def _open_writer(sym: str, date_str: str, writers: dict, first_flags: dict):
    """
    Открывает временный JSON-файл тикера и пишет начало массива '['.
    Возвращает file handle.
    """
    if sym in writers:
        return writers[sym]

    tmp_path = DATA_DIR / f"{sym}_{date_str}.json.tmp"
    f = open(tmp_path, "w", encoding="utf-8")
    f.write("[\n")
    writers[sym] = f
    first_flags[sym] = True
    return f


def main():
    if len(sys.argv) < 2:
        print("Использование: python tools/quik_csv_to_alor_format.py YYYY-MM-DD")
        sys.exit(1)

    date_str = sys.argv[1]

    if not CSV_PATH.exists():
        print(f"Файл не найден: {CSV_PATH}")
        sys.exit(1)

    try:
        day0 = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=MSK)
    except ValueError:
        print("Ошибка даты. Формат должен быть YYYY-MM-DD, пример: 2026-09-09")
        sys.exit(1)

    day1 = day0 + timedelta(days=1)
    ts0 = int(day0.timestamp() * 1000)
    ts1 = int(day1.timestamp() * 1000)

    file_size = CSV_PATH.stat().st_size
    print(f"[quik->json] файл: {CSV_PATH}")
    print(f"[quik->json] дата: {date_str}")
    print(f"[quik->json] размер: {file_size / 1024 / 1024:.1f} MB")
    print(f"[quik->json] окно МСК: {day0.isoformat()} -> {day1.isoformat()}")
    print("[quik->json] старт конвертации...")

    progress = Progress(file_size)

    writers = {}
    first_flags = {}
    counts = defaultdict(int)

    total_lines = 0
    total_trades = 0
    bad_lines = 0
    out_of_day = 0

    # Важно: newline="" не нужен, CSV простой ; и читается построчно.
    with open(CSV_PATH, "r", encoding="utf-8", errors="ignore") as src:
        for line in src:
            total_lines += 1

            parts = line.strip().split(";")
            if len(parts) < 5:
                bad_lines += 1
                continue

            sym = parts[0].strip()
            qty = _safe_float(parts[1])
            price = _safe_float(parts[2])
            side_raw = parts[3].strip().lower()
            ts = _safe_int_ms(parts[4])

            if not sym or qty is None or price is None or ts is None:
                bad_lines += 1
                continue

            if not (ts0 <= ts < ts1):
                out_of_day += 1
                continue

            side = "sell" if side_raw == "sell" else "buy"

            rec = {
                "id": 0,
                "symbol": sym,
                "qty": qty,
                "price": price,
                "time": datetime.fromtimestamp(ts / 1000, tz=MSK).isoformat(),
                "timestamp": ts,
                "side": side,
            }

            f = _open_writer(sym, date_str, writers, first_flags)

            if not first_flags[sym]:
                f.write(",\n")
            else:
                first_flags[sym] = False

            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")))

            counts[sym] += 1
            total_trades += 1

            # tell() на текстовом файле может быть дорогим, но для прогресса норм.
            # Чтобы не вызывать слишком часто — только каждые 2000 строк.
            if total_lines % 2000 == 0:
                try:
                    pos = src.tell()
                except OSError:
                    pos = 0
                progress.show(pos, total_trades, len(counts))

    # Закрываем JSON-массивы и переименовываем tmp -> json
    for sym, f in writers.items():
        f.write("\n]\n")
        f.close()

    for sym in writers.keys():
        tmp_path = DATA_DIR / f"{sym}_{date_str}.json.tmp"
        final_path = DATA_DIR / f"{sym}_{date_str}.json"
        tmp_path.replace(final_path)

    progress.show(file_size, total_trades, len(counts), force=True)
    print()

    print("\n[quik->json] готово")
    print(f"  строк прочитано: {total_lines}")
    print(f"  сделок за дату: {total_trades}")
    print(f"  тикеров: {len(counts)}")
    print(f"  битых строк: {bad_lines}")
    print(f"  вне даты: {out_of_day}")

    print("\n[quik->json] топ тикеров по числу сделок:")
    for sym, n in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:20]:
        print(f"  {sym:8s} {n}")

    print(f"\n[quik->json] файлы записаны в: {DATA_DIR}")


if __name__ == "__main__":
    main()