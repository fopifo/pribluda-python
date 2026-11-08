"""
Закрытие торгового дня: конвертация накопителя стрима
data/live_trades_YYYY-MM-DD.jsonl в data/{SYMBOL}_YYYY-MM-DD.json
(формат Алора, совместим с alor_download_day и v11 adaptive_qty).

Использование:
  python tools/end_of_day.py                  # вчера (МСК)
  python tools/end_of_day.py 2026-11-08       # указанная дата
  python tools/end_of_day.py 2026-11-08 --force  # перезаписать существующие

Безопасность: если data/{SYMBOL}_{DATE}.json уже существует (например,
скачан alor_download_day) — тикер пропускается без --force.
Jsonl-накопитель НЕ удаляется (остаётся архивом).
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")
BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"


def ms_to_iso(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, tz=MSK).isoformat(timespec="milliseconds")


def main():
    args = list(sys.argv[1:])
    force = "--force" in args
    args = [a for a in args if a != "--force"]
    date = args[0] if args else (datetime.now(MSK) - timedelta(days=1)).strftime("%Y-%m-%d")
    src = DATA / f"live_trades_{date}.jsonl"
    if not src.exists():
        print(f"[end_of_day] нет накопителя: {src}")
        sys.exit(1)

    by_sym = defaultdict(list)
    total = 0
    with open(src, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            by_sym[t["symbol"]].append(t)

    print(f"[end_of_day] дата={date} сделок={total} тикеров={len(by_sym)}")
    written = skipped = 0
    for sym, trades in sorted(by_sym.items()):
        trades.sort(key=lambda t: t["timestamp"])
        dst = DATA / f"{sym}_{date}.json"
        if dst.exists() and not force:
            skipped += 1
            print(f"  [skip] {sym}: {dst.name} уже существует (нужен --force)")
            continue
        out = []
        for t in trades:
            out.append({
                "id": t.get("id"),
                "orderno": 0,
                "symbol": sym,
                "board": t.get("board"),
                "qty": t.get("qty"),
                "price": t.get("price"),
                "time": ms_to_iso(t["timestamp"]),
                "timestamp": t["timestamp"],
                "oi": t.get("oi", 0),
                "existing": False,
                "side": t.get("side"),
            })
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
        written += 1
        first = datetime.fromtimestamp(trades[0]["timestamp"] / 1000, tz=MSK).strftime("%H:%M:%S")
        last = datetime.fromtimestamp(trades[-1]["timestamp"] / 1000, tz=MSK).strftime("%H:%M:%S")
        print(f"  [ok] {sym:6s} сделок={len(trades):6d} окно={first}-{last}")
    print(f"[end_of_day] готово: записано={written} пропущено={skipped} (jsonl не удалён)")


if __name__ == "__main__":
    main()