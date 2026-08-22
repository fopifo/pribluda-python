"""еплей сырой ленты конкурента по SNGSP (data/sngsp.csv)."""
import sys
from pathlib import Path
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from core.config import get_detector_configs
from detectors.interval_robot import IntervalRobotDetector

def secs(t):
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)

def main():
    csv_path = BASE / "data" / "sngsp.csv"
    cfgs = get_detector_configs("SNGSP", 20, {})
    dets = [IntervalRobotDetector("SNGSP", c) for c in cfgs]
    trades = []
    with open(csv_path, encoding="utf-8") as f:
        f.readline()
        for line in f:
            line = line.strip()
            if not line:
                continue
            p = line.split(";")
            if len(p) < 4:
                continue
            try:
                trades.append({"symbol": "SNGSP", "qty": int(p[1]),
                               "price": float(p[0]), "side": p[2],
                               "timestamp": secs(p[3]) * 1000})
            except ValueError:
                continue
    trades.sort(key=lambda t: t["timestamp"])
    signals = []
    for t in trades:
        for d in dets:
            signals.extend(d.on_trade(t))
    for d in dets:
        signals.extend(d.flush())
    print(f"Сделок: {len(trades)}, сигналов: {len(signals)}")
    for s in signals:
        print(f"  {s.side} qty={s.qty_variants} повт={s.repeats} int={s.interval_avg:.1f}s")

if __name__ == "__main__":
    main()
