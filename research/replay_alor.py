"""
Приблуда на python — офлайн-реплей ИСТИННЫХ сторон (Алор) через боевые
детекторы. Читает data/<TICKER>_YYYY-MM-DD.json (скачивается
tools/alor_download_day.py), кормит детекторы в хронологическом порядке.
История пишется изолированно в data/replay_alor_history.jsonl
(очищается в начале), боевые файлы НЕ трогает.

Использование:
    python research/replay_alor.py 2026-09-03                  # baseline
    python research/replay_alor.py 2026-09-03 --double-hit 1.0 # A/B фильтр двойных ударов

Дальше: python research/tw_compare.py 2026-09-03 --ours data/replay_alor_history.jsonl
"""
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
sys.path.insert(0, str(BASE))
from core.config import get_detector_configs
from core.ticker_settings import load_settings
from detectors.interval_robot import IntervalRobotDetector

HIST = BASE / "data" / "replay_alor_history.jsonl"


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
        print(f"\r[replay_alor] {self.label} [{bar}] {pct:3d}% | "
              f"{self.done}/{self.total} сделок | {speed:.0f}/s | ETA {eta:.0f}s",
              end="", flush=True)

    def close(self):
        print()


def main():
    args = sys.argv[1:]
    if not args or args[0].startswith("--"):
        print(__doc__)
        sys.exit(1)
    date_str = args[0]
    double_hit = None
    if "--double-hit" in args:
        i = args.index("--double-hit")
        double_hit = float(args[i + 1])

    files = sorted(DATA.glob(f"*_{date_str}.json"))
    if not files:
        print(f"[replay_alor] Нет файлов data/*_{date_str}.json. "
              f"Скачай: python tools/alor_download_day.py {date_str}")
        sys.exit(1)
    print(f"[replay_alor] файлов Алора: {len(files)}")
    if double_hit is not None:
        print(f"[replay_alor] РЕЖИМ A/B: min_double_hit_gap_sec={double_hit}")

    trades = []
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                lst = json.load(fp)
            if isinstance(lst, list):
                trades.extend(lst)
        except Exception as e:
            print(f"  ошибка чтения {f.name}: {e}")
    trades.sort(key=lambda t: t.get("timestamp") or 0)
    print(f"[replay_alor] сделок всего: {len(trades)}")

    settings = load_settings()
    dets = {}

    def det_for(sym):
        if sym not in dets:
            ov = dict(settings.get(sym, {}))
            if double_hit is not None:
                ov["min_double_hit_gap_sec"] = double_hit
            dets[sym] = [IntervalRobotDetector(sym, c)
                         for c in get_detector_configs(sym, ov.get("min_qty", 1), ov)]
            for d in dets[sym]:
                d._history_path = HIST  # изолированная история
        return dets[sym]

    if HIST.exists():
        HIST.unlink()

    fed = 0
    skip_sym = 0
    skip_qty = 0
    p = Progress(len(trades), "feed")
    for t in trades:
        p.update(1)
        sym = t.get("symbol")
        if sym not in settings:
            skip_sym += 1
            continue
        try:
            qty = int(float(t.get("qty")))
            price = float(t.get("price"))
            ts = int(t.get("timestamp"))
            side = t.get("side")
        except (TypeError, ValueError):
            continue
        ov = settings.get(sym, {})
        if qty < ov.get("min_qty", 10):
            skip_qty += 1
            continue
        for d in det_for(sym):
            d.on_trade({"symbol": sym, "qty": qty, "price": price,
                        "side": side, "timestamp": ts})
        fed += 1
    p.close()

    print(f"[replay_alor] скормлено={fed}, пропущено: sym={skip_sym}, qty={skip_qty}")
    print(f"[replay_alor] история: {HIST}")


if __name__ == "__main__":
    main()