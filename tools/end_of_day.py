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
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")
BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"


def ms_to_iso(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, tz=MSK).isoformat(timespec="milliseconds")


class Progress:
    """Прогресс-бар ASCII по числу/байтам."""

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
        print(f"\r[end_of_day] {self.label} [{bar}] {pct:3d}% | "
              f"{self.done}/{self.total} | {speed:.0f}/s | ETA {eta:.0f}s",
              end="", flush=True, file=sys.stderr)

    def close(self):
        print(file=sys.stderr)


def main():
    args = list(sys.argv[1:])
    force = "--force" in args
    args = [a for a in args if a != "--force"]
    date = args[0] if args else (datetime.now(MSK) - timedelta(days=1)).strftime("%Y-%m-%d")
    src = DATA / f"live_trades_{date}.jsonl"
    if not src.exists():
        print(f"[end_of_day] нет накопителя: {src}")
        sys.exit(1)

    file_size = src.stat().st_size
    by_sym = defaultdict(list)
    total = 0
    p_read = Progress(file_size, "чтение")
    with open(src, "rb") as f:
        for raw in f:
            p_read.update(len(raw))
            line = raw.decode("utf-8").strip()
            if not line:
                continue
            try:
                t = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            by_sym[t["symbol"]].append(t)
    p_read.close()

    print(f"[end_of_day] дата={date} сделок={total} тикеров={len(by_sym)}")
    written = skipped = 0
    syms = sorted(by_sym.items())
    total_syms = len(syms)
    t0 = time.time()
    for i, (sym, trades) in enumerate(syms, 1):
        trades.sort(key=lambda t: t["timestamp"])
        dst = DATA / f"{sym}_{date}.json"
        if dst.exists() and not force:
            skipped += 1
            print(f"  [skip {i}/{total_syms}] {sym}: {dst.name} уже существует (нужен --force)")
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
        dt = max(time.time() - t0, 1e-9)
        speed = i / dt
        eta = (total_syms - i) / max(speed, 1.0)
        print(f"  [ok {i}/{total_syms}] {sym:6s} сделок={len(trades):6d} "
              f"окно={first}-{last} | {speed:.1f}/s ETA {eta:.0f}s")
    print(f"[end_of_day] готово: записано={written} пропущено={skipped} (jsonl не удалён)")


if __name__ == "__main__":
    main()