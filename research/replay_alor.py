"""
Приблуда на python — оффлайн-реплей СТННых сторон (Алор) через боевые
детекторы. Читает data/live_trades_YYYY-MM-DD.jsonl ПОСТРОЧНО (без загрузки
в память), кормит детекторы в хронологическом порядке.
История пишется изолированно в data/replay_alor_history.jsonl
(очищается в начале), боевые файлы НЕ трогает.

Использование:
    python research/replay_alor.py 2026-09-03                  # baseline
    python research/replay_alor.py 2026-09-03 --double-hit 1.0 # A/B фильтр двойных ударов
    python research/replay_alor.py 2026-09-03 --no-min-qty     # без порога min_qty (потолок Recall)

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
    """ASCII прогресс-бар по байтам файла. В живой консоли — \r каждые 0.25с.
    В pipe (morning_report) — полная строка с \n каждые 10%."""

    def __init__(self, total_bytes, label):
        self.total = max(int(total_bytes), 1)
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
        speed = self.done / dt
        eta = (self.total - self.done) / max(speed, 1.0)
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        return (f"[replay_alor] {self.label} [{bar}] {pct:3d}% | "
                f"{self.done}/{self.total} байт | {speed/1024/1024:.1f} МБ/с | ETA {eta:.0f}с")

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
            sys.stderr.flush()
        else:
            print(self._line(), file=sys.stderr, flush=True)


def deduplicate_history(hist_path):
    """Читает историю, оставляет только одну запись на (symbol, side, start_ts)
    с максимальным repeats (финальный снимок серии)."""
    if not hist_path.exists():
        return 0, 0

    by_key = {}
    total_lines = 0
    with open(hist_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total_lines += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = (rec.get("symbol"), rec.get("side"), rec.get("start_ts"))
            reps = rec.get("repeats", 0)
            if key not in by_key or reps > by_key[key].get("repeats", 0):
                by_key[key] = rec

    deduped_lines = len(by_key)
    if deduped_lines == total_lines:
        return total_lines, 0

    with open(hist_path, "w", encoding="utf-8") as f:
        sorted_recs = sorted(by_key.values(), key=lambda x: x.get("start_ts", 0))
        for rec in sorted_recs:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    return total_lines, deduped_lines


def main():
    args = sys.argv[1:]
    if not args or args[0].startswith("--"):
        print(__doc__)
        sys.exit(1)
    date_str = args[0]
    double_hit = None
    no_min_qty = False
    noisy_qty = False
    
    if "--double-hit" in args:
        i = args.index("--double-hit")
        double_hit = float(args[i + 1])
    if "--no-min-qty" in args:
        no_min_qty = True
    if "--noisy-qty" in args:
        noisy_qty = True

    files = sorted(DATA.glob(f"live_trades_{date_str}.jsonl"))
    if not files:
        print(f"[replay_alor] Нет файлов data/live_trades_{date_str}.jsonl. "
              f"Скачай: python tools/alor_download_day.py {date_str}", file=sys.stderr)
        sys.exit(1)
    print(f"[replay_alor] файлов Алора: {len(files)}", file=sys.stderr)
    if double_hit is not None:
        print(f"[replay_alor] РЕЖИМ A/B: min_double_hit_gap_sec={double_hit}", file=sys.stderr)
    if no_min_qty:
        print("[replay_alor] РЕЖИМ A/B: без порога min_qty", file=sys.stderr)
    if noisy_qty:
        print("[replay_alor] РЕЖИМ A/B: фильтр noisy qty v12 C+B", file=sys.stderr)

    total_size = sum(f.stat().st_size for f in files)
    print(f"[replay_alor] общий размер: {total_size / 1024 / 1024:.1f} МБ (потоковое чтение)", file=sys.stderr)

    settings = load_settings()
    dets = {}

    def det_for(sym):
        if sym not in dets:
            ov = dict(settings.get(sym, {}))
            if double_hit is not None:
                ov["min_double_hit_gap_sec"] = double_hit
            if no_min_qty:
                ov["min_qty"] = 1
            if noisy_qty:
                ov["noisy_qty_filter"] = True
            dets[sym] = [IntervalRobotDetector(sym, c)
                         for c in get_detector_configs(sym, ov.get("min_qty", 1), ov)]
            for d in dets[sym]:
                d._history_path = HIST
        return dets[sym]

    if HIST.exists():
        HIST.unlink()

    fed = 0
    skip_sym = 0
    skip_qty = 0
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
                if not no_min_qty and qty < ov.get("min_qty", 10):
                    skip_qty += 1
                    continue
                
                for d in det_for(sym):
                    d.on_trade({"symbol": sym, "qty": qty, "price": price,
                                "side": side, "timestamp": ts})
                fed += 1
    p.close()

    print(f"[replay_alor] скормлено={fed}, пропущено: sym={skip_sym}, qty={skip_qty}", file=sys.stderr)

    total, deduped = deduplicate_history(HIST)
    if total > deduped:
        print(f"[replay_alor] дедупликация: было {total} записей, стало {deduped} (удалено {total - deduped} дублей)", file=sys.stderr)

    print(f"[replay_alor] история: {HIST}", file=sys.stderr)


if __name__ == "__main__":
    main()