"""
PeriodicityDetector — статистический поиск периодичности (аддитивный эксперимент).
НЕ заменяет interval_robot. Пишет в отдельную историю.
Группируем сделки по qty-бакетам: близкие qty (отношение к ЯКОРЮ <= qty_ratio,
не более max_variants штук) объединяются, чтобы чередование 57/58 не удваивало
период, но НЕ склеивала весь диапазон qty в один кластер.
Робот = устойчивый пик распределения зазоров + гармоники (пропуски).
"""
import json
import statistics
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from .base import Detector, Signal

MSK = ZoneInfo("Europe/Moscow")
_log = logging.getLogger("detector")


class PeriodicityDetector(Detector):
    name = "робот-период"

    def __init__(self, symbol, settings):
        super().__init__(symbol, settings)
        self.window_sec = settings.get("p_window_sec", 600.0)
        self.min_count = settings.get("p_min_count", 8)
        self.min_regularity = settings.get("p_min_regularity", 0.6)
        self.min_interval = settings.get("min_interval", 10.0)
        self.max_interval = settings.get("max_interval", 600.0)
        self.gap_tol_ratio = settings.get("p_gap_tol_ratio", 0.2)
        self.qty_ratio = settings.get("max_qty_ratio", 1.10)
        self.max_variants = settings.get("max_qty_variants", 4)
        self._trades = defaultdict(list)
        self._merged = {}
        self._history_path = Path(__file__).resolve().parent.parent / "data" / "replay_periodicity_history.jsonl"

    def on_trade(self, trade):
        self._trades[trade["side"]].append((trade["timestamp"] / 1000.0, trade["qty"]))
        return []

    def _analyze_window(self, side, rows):
        byq = defaultdict(list)
        for ts, q in rows:
            byq[q].append(ts)
        if not byq:
            return
        qtys = sorted(byq.keys())
        clusters = []
        cur = [qtys[0]]
        for q in qtys[1:]:
            # ЯКОРЬ = cur[0]; ограничиваем размер кластера, чтобы не склеить всё
            if cur[0] > 0 and q / cur[0] <= self.qty_ratio and len(cur) < self.max_variants:
                cur.append(q)
            else:
                clusters.append(cur)
                cur = [q]
        clusters.append(cur)

        for cl in clusters:
            tss = sorted(ts for q in cl for ts in byq[q])
            if len(tss) < self.min_count:
                continue
            gaps = [tss[i+1] - tss[i] for i in range(len(tss) - 1)]
            med = statistics.median(gaps)
            if med < self.min_interval or med > self.max_interval:
                continue
            tol = max(1.0, self.gap_tol_ratio * med)
            good = 0
            for g in gaps:
                k = round(g / med)
                if k >= 1 and abs(g - k * med) <= tol:
                    good += 1
            reg = good / len(gaps)
            if reg < self.min_regularity:
                continue
            key = (side, round(med, 1))
            st, en, n = tss[0], tss[-1], len(tss)
            rec = self._merged.get(key)
            if rec is None:
                self._merged[key] = {
                    "symbol": self.symbol, "side": side, "qty_variants": list(cl),
                    "interval_avg": round(med, 2), "repeats": n,
                    "start_ts": st, "end_ts": en,
                    "start_ms": int(st * 1000), "end_ms": int(en * 1000),
                    "regularity": round(reg, 2),
                }
            else:
                rec["repeats"] = max(rec["repeats"], n)
                for q in cl:
                    if q not in rec["qty_variants"] and len(rec["qty_variants"]) < self.max_variants:
                        rec["qty_variants"].append(q)
                rec["start_ts"] = min(rec["start_ts"], st)
                rec["end_ts"] = max(rec["end_ts"], en)
                rec["start_ms"] = int(rec["start_ts"] * 1000)
                rec["end_ms"] = int(rec["end_ts"] * 1000)
                rec["regularity"] = max(rec["regularity"], round(reg, 2))

    def flush(self):
        for side, rows in self._trades.items():
            if not rows:
                continue
            rows.sort(key=lambda x: x[0])
            t0, t1 = rows[0][0], rows[-1][0]
            step = self.window_sec / 2
            start = t0
            while start <= t1:
                end = start + self.window_sec
                win = [r for r in rows if start <= r[0] < end]
                if len(win) >= self.min_count:
                    self._analyze_window(side, win)
                start += step
        self._history_path.parent.mkdir(exist_ok=True)
        with open(self._history_path, "a", encoding="utf-8") as f:
            for rec in self._merged.values():
                rec["timestamp"] = datetime.now(MSK).isoformat(timespec="seconds")
                rec["preset"] = "periodicity"
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._trades.clear()
        self._merged.clear()
        return []

    def drain_confirms(self):
        return []

    def check_overdue(self, now_ts):
        return [], []

    def get_active_snapshot(self, now_ts):
        return []