"""
Приблуда на python — загрузка истории сделок (alltrades) с API Алор
за указанный день. Сохраняет data/<TICKER>_YYYY-MM-DD.json (список
сделок с полями id/symbol/qty/price/time/timestamp(мс)/side) — формат,
который читают research/compare_sides.py и research/replay_alor.py.

Использование:
    python tools/alor_download_day.py 2026-09-03              # все активные тикеры из ticker_settings.json
    python tools/alor_download_day.py 2026-09-03 T OZON X5    # только указанные

Идемпотентно: существующий файл пропускается. Токен: ALOR_REFRESH_TOKEN из .env.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE))

from alor_history_loader import (REFRESH_TOKEN, AlorTokenManager,
                                 day_range_unix, get_alltrades_history_page)
from core.ticker_settings import load_settings


class Progress:
    """Прогресс-бар по числу сделок. ASCII, % / скорость / ETA."""

    def __init__(self, total, label):
        self.total = max(int(total), 1)
        self.label = label
        self.done = 0
        self.t0 = time.time()
        self.last = 0.0

    def update(self, n):
        self.done += n
        now = time.time()
        if now - self.last < 0.25 and self.done < self.total:
            return
        self.last = now
        pct = min(self.done * 100 // self.total, 100)
        dt = max(now - self.t0, 1e-9)
        speed = self.done / dt
        eta = (self.total - self.done) / max(speed, 1.0)
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        print(f"\r[alor] {self.label} [{bar}] {pct:3d}% | "
              f"{self.done}/{self.total} сделок | {speed:.0f}/s | ETA {eta:.0f}s",
              end="", flush=True)

    def close(self):
        print()


def download_symbol(tm, symbol, date_from, date_to):
    """Все сделки тикера за день с прогресс-баром по пагинации."""
    trades = []
    offset = 0
    p = None
    while True:
        payload = get_alltrades_history_page(tm, symbol, date_from, date_to, offset=offset)
        total = payload.get("total", 0)
        if p is None:
            p = Progress(total, symbol)
        page = payload.get("list", [])
        if not page:
            break
        trades.extend(page)
        offset += len(page)
        p.update(len(page))
        if offset >= total:
            break
    p.close()
    return trades


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    date_str = args[0]
    tickers = args[1:]
    if not tickers:
        settings = load_settings()
        tickers = [t for t, ov in settings.items() if ov.get("active", True)]

    if not REFRESH_TOKEN:
        print("Ошибка: не найден ALOR_REFRESH_TOKEN в .env")
        sys.exit(1)

    day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    date_from, date_to = day_range_unix(day)
    tm = AlorTokenManager(REFRESH_TOKEN)

    print(f"[alor] дата {date_str}, тикеров: {len(tickers)}")
    for i, sym in enumerate(tickers, 1):
        out = BASE / "data" / f"{sym}_{date_str}.json"
        if out.exists():
            print(f"[{i}/{len(tickers)}] {sym}: уже есть {out.name}, пропускаю")
            continue
        print(f"[{i}/{len(tickers)}] {sym}: качаю...", flush=True)
        try:
            trades = download_symbol(tm, sym, date_from, date_to)
        except Exception as e:
            print(f"  ошибка: {e}")
            continue
        with open(out, "w", encoding="utf-8") as f:
            json.dump(trades, f, ensure_ascii=False)
        print(f"  сохранено {len(trades)} сделок -> {out.name}")
        time.sleep(0.2)
    print("[alor] Готово.")


if __name__ == "__main__":
    main()