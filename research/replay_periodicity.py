"""
research/replay_periodicity.py — аддитивный прогон НОВОГО PeriodicityDetector.
Читает ту же ленту Алора потоково, пишет в data/replay_periodicity_history.jsonl.
НЕ трогает replay_alor / interval_robot / боевые файлы.
Запуск: python research/replay_periodicity.py 2026-11-18
Сравнение: python research/tw_compare.py 2026-11-18 --ours data/replay_periodicity_history.jsonl
"""
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
sys.path.insert(0, str(BASE))
from core.ticker_settings import load_settings
from detectors.periodicity_detector import PeriodicityDetector

HIST = DATA / "replay_periodicity_history.jsonl"


class Progress:
    def __init__(self, total, label):
        self.total = max(int(total), 1)
        self.label = label
        self.done = 0
        self.t0 = time.time()
        self.last_tick = 0.0
        self.last_pct = -1
        self.is_tty = sys.stderr.isatty()

    def _line(self):
        now = time.time()
        pct = min(self.done * 100 // self.total, 100)
        dt = max(now - self.t0, 1e-9)
        sp = self.done / dt
        eta = (self.total - self.done) / max(sp, 1.0)
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        return (f"[replay_period] {self.label} [{bar}] {pct:3d}% | "
                f"{self.done}/{self.total} байт | {sp/1024/1024:.1f} МБ/с | ETA {eta:.0f}с")

    def update(self, n):
        self.done += n
        now = time.time()
        pct = min(self.done * 100 // self.total, 100)
        if self.is_tty:
            if now - self.last_tick < 0.25 and self.done < self.total:
                return
            self.last_tick = now
            sys.stderr.write("\r" + self._line())
            sys.stderr.flush()
        else:
            if pct // 10 != self.last_pct // 10:
                self.last_pct = pct
                print(self._line(), file=sys.stderr, flush=True)

    def close(self):
        if self.is_tty:
            sys.stderr.write("\r" + self._line() + "\n")
        else:
            print(self._line(), file=sys.stderr, flush=True)


def main():
    args = sys.argv[1:]
    if not args or args[0].startswith("--"):
        print(__doc__)
        sys.exit(1)
    date_str = args[0]
    files = sorted(DATA.glob(f"live_trades_{date_str}.jsonl"))
    if not files:
        print(f"[replay_period] нет live_trades_{date_str}.jsonl", file=sys.stderr)
        sys.exit(1)

    settings = load_settings()
    dets = {}

    def det_for(sym):
        if sym not in dets:
            ov = dict(settings.get(sym, {}))
            dets[sym] = PeriodicityDetector(sym, ov)
        return dets[sym]

    if HIST.exists():
        HIST.unlink()

    total_size = sum(f.stat().st_size for f in files)
    print(f"[replay_period] размер: {total_size/1024/1024:.1f} МБ (потоково)", file=sys.stderr)
    fed = 0
    p = Progress(total_size, "feed")
    for f in files:
        with open(f, "r", encoding="utf-8") as fp:
            for line in fp:
                p.update(len(line))
                line = line.strip()
                if not line:
                    continue
                try:
                    t = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sym = t.get("symbol")
                if sym not in settings:
                    continue
                try:
                    qty = int(float(t.get("qty")))
                    ts = int(t.get("timestamp"))
                    side = t.get("side")
                except (TypeError, ValueError):
                    continue
                if qty < settings.get(sym, {}).get("min_qty", 3):
                    continue
                det_for(sym).on_trade({"symbol": sym, "qty": qty,
                                       "side": side, "timestamp": ts})
                fed += 1
    p.close()

    for d in dets.values():
        d.flush()
    print(f"[replay_period] скормлено={fed}, детекторов={len(dets)}", file=sys.stderr)
    print(f"[replay_period] история: {HIST}", file=sys.stderr)


if __name__ == "__main__":
    main()