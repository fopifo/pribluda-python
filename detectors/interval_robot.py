"""
Приблуда на python — детектор периодичности (роботов), персистентный.
С логированием в output/detector.log для отладки.
MAX_ACTIVE_PER_SIDE = 500 (оптимизация производительности).
ИСПРАВЛЕНО: get_active_snapshot теперь использует min_display_repeats
(дефолт 2 — показываем кандидатов, как в v5).
ВОССТАНОВЛЕНО: блок warnings.append в check_overdue (был потерян).
"""
import logging
import statistics
import os
from pathlib import Path
from .base import Detector, Signal

# --- Настройка логгера ---
_log = logging.getLogger("detector")
if not _log.handlers:
    _logdir = Path(__file__).resolve().parent.parent / "output"
    _logdir.mkdir(exist_ok=True)
    _handler = logging.FileHandler(_logdir / "detector.log", encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    _log.addHandler(_handler)
    _log.setLevel(logging.INFO)


class Candidate:
    __slots__ = ("qty_variants", "count", "start_ts", "last_ts",
                 "last_interval", "intervals", "warned",
                 "first_price", "last_price", "price_counts", "priced_hits",
                 "sum_qty", "qty_counts")
    def __init__(self, qty, ts, price=None):
        self.qty_variants = {qty}
        self.count = 1
        self.start_ts = ts
        self.last_ts = ts
        self.last_interval = None
        self.intervals = []
        self.warned = False
        self.first_price = price
        self.last_price = price
        self.price_counts = {}
        if price is not None: self.price_counts[price] = 1
        self.priced_hits = 1 if price is not None else 0
        self.sum_qty = qty
        self.qty_counts = {qty: 1}


class IntervalRobotDetector(Detector):
    name = "робот-интервал"
    MAX_ACTIVE_PER_SIDE = 500

    def __init__(self, symbol, settings):
        super().__init__(symbol, settings)
        self.min_qty = settings["min_qty"]
        self.min_repeats = settings["min_repeats"]
        self.min_interval = settings.get("min_interval")
        if self.min_interval is None: self.min_interval = 1.0
        self.max_interval = settings.get("max_interval")
        if self.max_interval is None: self.max_interval = 600
        self.max_qty_variants = settings.get("max_qty_variants", 2)
        self.max_qty_ratio = settings.get("max_qty_ratio", 1.10)
        self.interval_tolerance = settings.get("interval_tolerance")
        self.ignore_qty = settings.get("ignore_qty", False)
        self.time_window_sec = settings.get("time_window_sec", 0.0)
        self.close_after_misses = settings.get("close_after_misses", 6)
        self.max_series = settings.get("max_series", 100000)
        
        # Н-013: допуск для коротких интервалов
        self.short_interval_tolerance = settings.get("short_interval_tolerance", 0.12)
        self.short_interval_threshold = settings.get("short_interval_threshold", 60.0)
        
        # Н-004: фильтр стабильного qty для длинных интервалов
        self.long_interval_threshold = settings.get("long_interval_threshold", 120.0)
        self.stable_qty_required = settings.get("stable_qty_required", False)
        self.stable_qty_ratio = settings.get("stable_qty_ratio", 0.8)
        
        # Минимальное число повторов для отображения в UI (дефолт 2 — кандидаты)
        self.min_display_repeats = settings.get("min_display_repeats", 2)
        
        preset_name = settings.get("preset_name")
        self.preset_name = preset_name or ""
        if preset_name: self.name = f"робот-интервал[{preset_name}]"
        self.active = {}
        self.index = {}
        
        # Н-010: путь к истории роботов
        self._history_path = Path(__file__).resolve().parent.parent / "data" / "robots_history.jsonl"
        self._history_path.parent.mkdir(exist_ok=True)
        
        _log.info(f"[{symbol}] INIT: min_qty={self.min_qty}, min_repeats={self.min_repeats}, "
                  f"min_interval={self.min_interval}, max_interval={self.max_interval}, "
                  f"short_tol={self.short_interval_tolerance}<{self.short_interval_threshold}s, "
                  f"long_thresh={self.long_interval_threshold}s, stable_qty={self.stable_qty_required}, "
                  f"min_display={self.min_display_repeats}")

    def _finalize(self, side, candidate):
        avg_interval = (candidate.last_ts - candidate.start_ts) / max(candidate.count - 1, 1)
        jitter_ms = statistics.pstdev(candidate.intervals) * 1000 if len(candidate.intervals) >= 2 else None
        stability_ratio = None
        if len(candidate.intervals) >= 2 and self.time_window_sec > 0:
            med = statistics.median(candidate.intervals)
            good = sum(1 for iv in candidate.intervals if abs(iv - med) <= self.time_window_sec)
            stability_ratio = good / len(candidate.intervals)
        return Signal(detector_name=self.name, symbol=self.symbol, side=side,
                      qty_variants=sorted(candidate.qty_variants), repeats=candidate.count,
                      interval_avg=avg_interval, start_ts=candidate.start_ts,
                      end_ts=candidate.last_ts, jitter_ms=jitter_ms,
                      stability_ratio=stability_ratio)

    def _register(self, side, candidate):
        self.active.setdefault(side, []).append(candidate)
        if self.ignore_qty: return
        si = self.index.setdefault(side, {})
        for q in candidate.qty_variants: si.setdefault(q, []).append(candidate)

    def _unregister(self, side, candidate):
        al = self.active.get(side)
        if al:
            try: al.remove(candidate)
            except ValueError: pass
        if self.ignore_qty: return
        si = self.index.get(side, {})
        for q in candidate.qty_variants:
            b = si.get(q)
            if b:
                try: b.remove(candidate)
                except ValueError: pass
                if not b: del si[q]

    def _index_new_variant(self, side, candidate, qty):
        if self.ignore_qty: return
        self.index.setdefault(side, {}).setdefault(qty, []).append(candidate)

    def _prune_dead(self, side, now_ts):
        signals = []
        close_threshold = self.max_interval * self.close_after_misses
        al = self.active.get(side, [])
        while al and now_ts - al[0].last_ts > close_threshold:
            c = al.pop(0)
            if c.count >= self.min_repeats:
                signals.append(self._finalize(side, c))
            self._unregister(side, c)
        return signals

    def _enforce_cap(self, side):
        al = self.active.get(side, [])
        overflow = len(al) - self.MAX_ACTIVE_PER_SIDE
        if overflow <= 0: return []
        to_evict = sorted(al, key=lambda c: (c.count, c.start_ts))[:overflow]
        signals = []
        for c in to_evict:
            if c.count >= self.min_repeats:
                signals.append(self._finalize(side, c))
            self._unregister(side, c)
        return signals

    def _qty_fits_loose(self, candidate, qty):
        if self.max_qty_ratio is None: return True
        low = min(*candidate.qty_variants, qty)
        high = max(*candidate.qty_variants, qty)
        return high / low <= self.max_qty_ratio

    def _interval_fits(self, candidate, interval):
        if interval is None: return False
        if self.min_interval is None: return False
        if self.max_interval is None: return False
        if interval < self.min_interval or interval > self.max_interval: return False
        
        # Н-013: адаптивный допуск для коротких интервалов
        if candidate.last_interval is not None and candidate.last_interval < self.short_interval_threshold:
            tol = self.short_interval_tolerance
        else:
            tol = self.interval_tolerance
        
        if tol is None or candidate.last_interval is None: return True
        low = candidate.last_interval * (1 - tol)
        high = candidate.last_interval * (1 + tol)
        return low <= interval <= high

    def _find_match(self, side, qty, ts):
        if not self.ignore_qty:
            for c in self.index.get(side, {}).get(qty, []):
                iv = ts - c.last_ts
                if self._interval_fits(c, iv):
                    return c
        for c in reversed(self.active.get(side, [])):
            iv = ts - c.last_ts
            if iv > self.max_interval:
                break
            if not self._interval_fits(c, iv):
                continue
            if self.ignore_qty:
                return c
            if qty in c.qty_variants:
                return c
            if len(c.qty_variants) < self.max_qty_variants and self._qty_fits_loose(c, qty):
                return c
        return None

    def _apply_price(self, candidate, qty, price):
        candidate.sum_qty += qty
        candidate.qty_counts[qty] = candidate.qty_counts.get(qty, 0) + 1
        if price is None: return
        candidate.last_price = price
        candidate.priced_hits += 1
        candidate.price_counts[price] = candidate.price_counts.get(price, 0) + 1

    def _metro(self, candidate):
        if not candidate.intervals: return []
        med = statistics.median(candidate.intervals)
        tol = self.interval_tolerance or 0.1
        out = []
        for iv in candidate.intervals[-3:]:
            dev = abs(iv - med) / med if med > 0 else 0
            st = "ok" if dev <= tol else ("warn" if dev <= 2 * tol else "bad")
            out.append((round(iv * 1000), st))
        return out

    def on_trade(self, trade):
        qty = trade["qty"]
        if qty < self.min_qty: return []
        side = trade["side"]
        ts = trade["timestamp"] / 1000.0
        price = trade.get("price")
        signals = self._prune_dead(side, ts)
        match = self._find_match(side, qty, ts)
        if match is not None:
            iv = ts - match.last_ts
            match.intervals.append(iv)
            match.last_interval = iv
            if qty not in match.qty_variants:
                match.qty_variants.add(qty)
                self._index_new_variant(side, match, qty)
            match.count += 1
            match.last_ts = ts
            match.warned = False
            self._apply_price(match, qty, price)
            al = self.active.get(side, [])
            try:
                if al[-1] is not match:
                    al.remove(match)
                    al.append(match)
            except ValueError:
                al.append(match)
            _log.info(f"[{self.symbol}] MATCH: side={side}, qty={qty}, interval={iv:.2f}s, "
                      f"repeats={match.count}, variants={sorted(match.qty_variants)}")
            if match.count == self.min_repeats:
                _log.info(f"[{self.symbol}] *** CONFIRMED ***: side={side}, repeats={match.count}, "
                          f"interval={match.last_interval:.2f}s, variants={sorted(match.qty_variants)}")
            if match.count >= self.max_series:
                signals.append(self._finalize(side, match))
                self._unregister(side, match)
        else:
            new_c = Candidate(qty, ts, price)
            self._register(side, new_c)
            _log.info(f"[{self.symbol}] NEW: side={side}, qty={qty}, price={price}")
            signals.extend(self._enforce_cap(side))
        return signals

    def check_overdue(self, now_ts):
        """Проверяет просроченные серии. Возвращает signals и warnings.
        ВОССТАНОВЛЕНО: блок warnings.append — был потерян при ошибочном копировании."""
        signals, warnings = [], []
        close_threshold = self.max_interval * self.close_after_misses
        for side, cands in list(self.active.items()):
            for c in list(cands):
                gap = now_ts - c.last_ts
                if gap > close_threshold:
                    if c.count >= self.min_repeats:
                        signals.append(self._finalize(side, c))
                    self._unregister(side, c)
                    continue
                if c.count < self.min_repeats: continue
                if c.last_interval is not None:
                    # Н-013: адаптивный допуск (короткие vs длинные интервалы)
                    if c.last_interval < self.short_interval_threshold:
                        tol = self.short_interval_tolerance
                    else:
                        tol = self.interval_tolerance
                    if tol is not None:
                        max_fit = c.last_interval * (1 + tol)
                        if gap > max_fit and not c.warned:
                            c.warned = True
                            warnings.append({
                                "symbol": self.symbol,
                                "side": side,
                                "qty_variants": sorted(c.qty_variants),
                                "gap_sec": round(gap, 1),
                                "max_fit_sec": round(max_fit, 1),
                                "просрочка": f"{gap:.1f}s > {max_fit:.1f}s",
                            })
                            _log.info(f"[{self.symbol}] OVERDUE: side={side}, "
                                      f"gap={gap:.1f}s > max_fit={max_fit:.1f}s, "
                                      f"variants={sorted(c.qty_variants)}")
        return signals, warnings

    def get_active_snapshot(self, now_ts):
        rows = []
        for side, cands in self.active.items():
            for c in cands:
                # ИСПРАВЛЕНО: используем min_display_repeats (дефолт 2)
                # Это сохраняет блоки long2/short2 в UI (кандидаты 2-3 повтора)
                if c.count < self.min_display_repeats: continue
                
                seconds_to_next = (c.last_ts + c.last_interval - now_ts) if c.last_interval is not None else None
                jitter_ms = statistics.pstdev(c.intervals) * 1000 if len(c.intervals) >= 2 else None
                same_price_ratio = None
                if c.priced_hits > 0 and c.price_counts: same_price_ratio = max(c.price_counts.values()) / c.priced_hits
                price_shift = None
                if c.first_price is not None and c.last_price is not None: price_shift = c.last_price - c.first_price
                rows.append({
                    "symbol": self.symbol, "preset": self.preset_name, "side": side,
                    "qty_variants": sorted(c.qty_variants), "interval": c.last_interval,
                    "repeats": c.count, "seconds_to_next": seconds_to_next,
                    "start_ts": c.start_ts, "jitter_ms": jitter_ms,
                    "price_first": c.first_price, "price_last": c.last_price,
                    "price_shift": price_shift, "same_price_ratio": same_price_ratio,
                    "sum_qty": c.sum_qty, "metro": self._metro(c),
                })
        return rows

    def flush(self):
        signals = []
        for side, cands in list(self.active.items()):
            for c in cands:
                if c.count >= self.min_repeats: signals.append(self._finalize(side, c))
        self.active.clear(); self.index.clear()
        return signals