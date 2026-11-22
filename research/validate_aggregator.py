"""
research/validate_aggregator.py — быстрая оффлайн-валидация агрегатора.
Читает готовые эпизоды из replay_alor_history.jsonl, прогоняет через 
RobotAggregator и сравнивает с TW-эталоном. Время выполнения: ~2-5 сек.
Запуск: python research/validate_aggregator.py 2026-11-18
"""
import sys
import json
import time
import math
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from core.robot_aggregator import RobotAggregator

MSK = ZoneInfo("Europe/Moscow")
DATA_DIR = BASE / "data"


class Progress:
    def __init__(self, total, label):
        self.total = max(int(total), 1)
        self.label = label
        self.done = 0
        self.t0 = time.time()
        self.last = 0.0
        self.lp = -1
        self.tty = sys.stderr.isatty()

    def _line(self):
        pct = min(self.done * 100 // self.total, 100)
        dt = max(time.time() - self.t0, 1e-9)
        sp = self.done / dt
        eta = (self.total - self.done) / max(sp, 1)
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        return f"[validate] {self.label} [{bar}] {pct:3d}% | {self.done}/{self.total} | {sp:.0f}/s | ETA {eta:.0f}s"

    def update(self, n=1):
        self.done += n
        pct = min(self.done * 100 // self.total, 100)
        now = time.time()
        if self.tty:
            if now - self.last < 0.1 and self.done < self.total:
                return
            self.last = now
            sys.stderr.write("\r" + self._line())
            sys.stderr.flush()
        else:
            if pct // 10 != self.lp // 10:
                self.lp = pct
                print(self._line(), file=sys.stderr, flush=True)

    def close(self):
        if self.tty:
            sys.stderr.write("\r" + self._line() + "\n")
        else:
            print(self._line(), file=sys.stderr, flush=True)


def load_tw_robots(date_str):
    tw_path = DATA_DIR / f"tw_robots_{date_str}.jsonl"
    if not tw_path.exists():
        return []
    tw_data = json.loads(tw_path.read_text(encoding="utf-8"))
    if isinstance(tw_data, dict) and "payload" in tw_data and "robots" in tw_data["payload"]:
        robots = tw_data["payload"]["robots"]
    elif isinstance(tw_data, list):
        robots = tw_data
    else:
        robots = [tw_data]
    
    filtered = []
    for r in robots:
        interval = float(r.get("interval", 0)) / 1000.0
        if 2.0 <= interval <= 60.0:
            filtered.append(r)
    return filtered


def debug_confidence(ep):
    """Отладочный расчёт confidence для одного эпизода."""
    hits = ep.get("repeats", 0)
    start_ts = ep.get("start_ts", 0)
    end_ts = ep.get("end_ts", 0)
    duration = max(end_ts - start_ts, 1)
    regularity = ep.get("regularity", 0.5)
    qty_variants = ep.get("qty_variants", [])
    
    qty_stability = 1.0
    if len(qty_variants) > 1:
        min_q, max_q = min(qty_variants), max(qty_variants)
        if min_q > 0:
            ratio = max_q / min_q
            if ratio < 1.1: qty_stability = 1.0
            elif ratio < 1.3: qty_stability = 0.8
            elif ratio < 2.0: qty_stability = 0.6
            else: qty_stability = 0.3

    hits_score = min(1.0, math.log(max(hits, 1)) / math.log(30))
    duration_score = min(1.0, math.log(max(duration, 1)) / math.log(600))
    
    conf = 0.3 * regularity + 0.3 * qty_stability + 0.2 * hits_score + 0.2 * duration_score
    
    return {
        "hits": hits, "dur": duration, "reg": regularity, 
        "qty_stab": qty_stability, "hits_sc": hits_score, "dur_sc": duration_score, "conf": conf
    }


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else "2026-11-18"
    history_path = DATA_DIR / "replay_alor_history.jsonl"
    
    if not history_path.exists():
        print(f"[!] нет {history_path.name}. Сначала запустите replay_alor.py или morning_report.py")
        return

    size = history_path.stat().st_size
    pr = Progress(size, "read_history")
    
    aggregator = RobotAggregator()
    episodes_count = 0
    debug_samples = []

    with open(history_path, "r", encoding="utf-8") as f:
        for line in f:
            pr.update(len(line))
            line = line.strip()
            if not line:
                continue
            try:
                ep = json.loads(line)
            except json.JSONDecodeError:
                continue
            
            aggregator.update_from_snapshot(ep, ep.get("end_ts", time.time()))
            episodes_count += 1
            
            if len(debug_samples) < 5:
                debug_samples.append((ep, debug_confidence(ep)))

    pr.close()

    print("\n" + "="*70)
    print("ОТЛАДКА: Почему confidence низкий? (первые 5 эпизодов)")
    print("="*70)
    for ep, dbg in debug_samples:
        print(f"{ep.get('symbol'):6s} {ep.get('side'):4s} hits={dbg['hits']:4d} dur={dbg['dur']:6.1f}s "
              f"reg={dbg['reg']:.2f} qty_stab={dbg['qty_stab']:.2f} h_sc={dbg['hits_sc']:.2f} "
              f"d_sc={dbg['dur_sc']:.2f} => CONF={dbg['conf']:.2f} | qty={ep.get('qty_variants')}")

    final_robots = aggregator.get_all(min_confidence=0.5)
    
    print(f"\n{'='*70}")
    print(f"ОБРАБОТАНО ЭПИЗОДОВ: {episodes_count}")
    print(f"НАЙДЕНО РОБОТОВ (conf >= 0.5): {len(final_robots)}")
    print(f"НАЙДЕНО РОБОТОВ (conf >= 0.3): {len(aggregator.get_all(min_confidence=0.3))}")
    print(f"{'='*70}")
    
    # Показываем топ по confidence, даже если он < 0.5
    all_robots = aggregator.get_all(min_confidence=0.0)
    print(f"{'sym':7s}{'side':5s}{'int':>7s}{'hits':>7s}{'dur_м':>8s}{'conf':>7s}{'reg':>7s}  qty")
    for r in sorted(all_robots, key=lambda x: -x['confidence'])[:15]:
        print(f"{r['symbol']:7s}{r['side']:5s}{r['interval']:7.1f}{r['hits']:7d}"
              f"{r['duration_min']:8.1f}{r['confidence']:7.2f}{r['regularity']:7.2f}"
              f"  {r['qty_variants'][:3]}")

    tw_robots = load_tw_robots(date_str)
    if tw_robots:
        print(f"\n{'='*70}")
        print(f"СРАВНЕНИЕ С TW-ЭТАЛОНОМ ({len(tw_robots)} роботов)")
        print(f"{'='*70}")
        tw_by_key = defaultdict(list)
        for r in tw_robots:
            ticker = r.get("ticker")
            side = "buy" if r.get("isBuy") else "sell"
            interval = float(r.get("interval", 0)) / 1000.0
            tw_by_key[(ticker, side)].append(interval)

        tp = 0
        matched = set()
        for r in all_robots: # Используем all_robots для поиска совпадений, чтобы увидеть FN
            key = (r["symbol"], r["side"])
            if key in tw_by_key:
                for tw_int in tw_by_key[key]:
                    if abs(r["interval"] - tw_int) <= max(2.0, tw_int * 0.2):
                        tp += 1
                        matched.add((r["symbol"], r["side"], tw_int))
                        break

        fp = len(final_robots) # FP считаем только для тех, кто прошёл порог 0.5
        fn = sum(len(v) for v in tw_by_key.values()) - len(matched)
        
        # Пересчитаем precision/recall для порога 0.3, если 0.5 пуст
        threshold = 0.5 if len(final_robots) > 0 else 0.3
        passed = aggregator.get_all(min_confidence=threshold)
        tp_thresh = sum(1 for r in passed if (r["symbol"], r["side"], round(r["interval"])) in 
                        {(m[0], m[1], round(m[2])) for m in matched})
        
        precision = tp_thresh / len(passed) if len(passed) > 0 else 0.0
        recall = len(matched) / sum(len(v) for v in tw_by_key.values()) if sum(len(v) for v in tw_by_key.values()) > 0 else 0.0

        print(f"Порог conf >= {threshold}: TP={tp_thresh}  FP={len(passed)-tp_thresh}  FN={fn}")
        print(f"Precision={precision:.1%}  Recall={recall:.1%}")
    else:
        print(f"\n[!] TW-эталон не загружен (нет {DATA_DIR/f'tw_robots_{date_str}.jsonl'})")


if __name__ == "__main__":
    main()