"""
core/robot_aggregator.py — онлайн-агрегатор роботов (real-time confidence).
Считает confidence на основе regularity/qty_stability/hits в реальном времени.
Цель: увидеть робота через 2-3 минуты (8-15 ударов), а не ждать 30 минут.
"""
import math
from datetime import datetime
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")


class RobotAggregator:
    def __init__(self):
        self.entities = {}  # (symbol, side, interval_bucket) -> state

    def update_from_snapshot(self, snapshot: dict, now_ts: float):
        """
        Обновляет агрегатор из snapshot детектора.
        snapshot содержит: symbol, side, interval или interval_avg, qty_variants, repeats, start_ts, end_ts, regularity
        """
        symbol = snapshot.get("symbol")
        side = snapshot.get("side")
        # Поддержка как interval (из get_active_snapshot), так и interval_avg (из истории)
        interval = snapshot.get("interval_avg", snapshot.get("interval", 0.0))
        qty_variants = snapshot.get("qty_variants", [])
        repeats = snapshot.get("repeats", 0)
        start_ts = snapshot.get("start_ts", now_ts)
        end_ts = snapshot.get("end_ts", now_ts)
        regularity = snapshot.get("regularity", 0.5)

        if not symbol or not side or not interval or interval <= 0:
            return

        # Округляем интервал до 5 секунд для группировки
        interval_bucket = round(interval / 5.0) * 5.0
        key = (symbol, side, interval_bucket)

        if key not in self.entities:
            self.entities[key] = {
                "symbol": symbol,
                "side": side,
                "interval": interval,
                "qty_variants": list(qty_variants),
                "start_ts": start_ts,
                "end_ts": end_ts,
                "hits": repeats,
                "regularity": regularity,
                "qty_stability": self._calc_qty_stability(qty_variants),
                "last_seen": end_ts,
            }
        else:
            state = self.entities[key]
            state["end_ts"] = max(state["end_ts"], end_ts)
            state["hits"] = max(state["hits"], repeats)
            state["regularity"] = max(state["regularity"], regularity)
            state["last_seen"] = max(state["last_seen"], end_ts)
            
            for q in qty_variants:
                if q not in state["qty_variants"]:
                    state["qty_variants"].append(q)
            state["qty_stability"] = self._calc_qty_stability(state["qty_variants"])

    def _calc_qty_stability(self, qty_variants: list) -> float:
        if not qty_variants:
            return 0.0
        if len(qty_variants) == 1:
            return 1.0
        min_q, max_q = min(qty_variants), max(qty_variants)
        if min_q == 0:
            return 0.0
        ratio = max_q / min_q
        if ratio < 1.1:
            return 1.0
        elif ratio < 1.3:
            return 0.8
        elif ratio < 2.0:
            return 0.6
        else:
            return 0.3

    def _calc_confidence(self, state: dict) -> float:
        regularity = state.get("regularity", 0.0)
        qty_stability = state.get("qty_stability", 0.0)
        hits = state.get("hits", 0)
        duration = state.get("end_ts", 0) - state.get("start_ts", 0)

        hits_score = min(1.0, math.log(max(hits, 1)) / math.log(30))
        duration_score = min(1.0, math.log(max(duration, 1)) / math.log(600))

        confidence = (
            0.3 * regularity +
            0.3 * qty_stability +
            0.2 * hits_score +
            0.2 * duration_score
        )

        return confidence

    def _serialize(self, state: dict) -> dict:
        duration_min = (state["end_ts"] - state["start_ts"]) / 60.0
        return {
            "symbol": state["symbol"],
            "side": state["side"],
            "interval": round(state["interval"], 1),
            "qty_variants": state["qty_variants"][:5],
            "hits": state["hits"],
            "duration_min": round(duration_min, 1),
            "regularity": round(state["regularity"], 2),
            "qty_stability": round(state["qty_stability"], 2),
            "confidence": round(self._calc_confidence(state), 2),
            "last_seen": datetime.fromtimestamp(state["last_seen"], tz=MSK).strftime("%H:%M:%S"),
        }

    def get_all(self, min_confidence: float = 0.5) -> list:
        results = []
        for state in self.entities.values():
            confidence = self._calc_confidence(state)
            if confidence >= min_confidence:
                results.append(self._serialize(state))
        results.sort(key=lambda x: -x["confidence"])
        return results