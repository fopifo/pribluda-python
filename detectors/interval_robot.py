"""
Приблуда на python — детектор периодичности (роботов), персистентный.
С логированием в output/detector.log для отладки.
MAX_ACTIVE_PER_SIDE = 500 (оптимизация производительности).
v5: история (Н-010) пишется в МОМЕНТ ПОДТВЕРЖДЕНИЯ (count==min_repeats),
а не при закрытии серии — статистика копится в течение дня.
v7: КРАТНЫЕ ИНТЕРВАЛЫ — пропуск удара больше не рвёт серию:
интервал ≈ k*база (k=2..interval_mult_max) принимается; если первый
интервал сам был кратным (поймали с пропуском), база уточняется вниз.
Кратные включаются ТОЛЬКО при базе >= short_interval_threshold (10с),
чтобы НЕ трогать поведение быстрых серий. CD/INT считаются от базы.
v8: ФИЛЬТР ДЖИТТЕРА — серии с джиттером > jitter_ratio_max × интервал
считаются мусором и НЕ репортятся (не попадают в сигналы и в снапшот).
v9: GRID LOCK — после подтверждения серия защёлкивается на сетку:
удар засчитывается, если попадает в окно ±grid_tolerance_ms вокруг
любого тика k*база (k до max_interval/base). Пропуски не рвут серию.
До подтверждения поведение как в v7/v8 (тесты и быстрые серии не тронуты).
v9.1: время в _write_history явно в МСК (было голый datetime.now()).
v10: ФИЛЬТР ДВОЙНЫХ УДАРОВ — если интервал между текущей сделкой и
последним ударом найденной серии < min_double_hit_gap_sec (1.0с),
считать это тем же ударом (шум, burst-sell), не обновлять серию.
Корень проблемы CNRU: тройные удары с gap=0 ломают базу серии.
v10.1: порог уменьшен с 2.0 до 1.0с (было слишком агрессивно, TP упал).
v10.2: ИСТОРИЯ В НАТУРАЛЬНЫХ МС — в robots_history.jsonl дополнительно
пишутся start_ms/end_ms/interval_ms (epoch ms, напрямую из ленты QUIK,
без конверсий на стороне потребителей). Секундные поля (start_ts/end_ts/
interval_avg) оставлены для старой статистики и GUI. Критерии детекции
НЕ изменены.
v10.3: ЛОГИРОВАНИЕ ВСЕХ СДЕЛОК — в detector.log пишутся ВСЕ сделки
(и те, что ниже min_qty) с флагом passed_min_qty. Для анализа FN:
видим, что происходит в зоне реза (SVCB 13 лотов, FLOT 16 лотов и т.д.).
"""
import json
import logging
import statistics
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from .base import Detector, Signal

MSK = ZoneInfo("Europe/Moscow")

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
                 "last_interval", "base_interval", "intervals", "warned",
                 "first_price", "last_price", "price_counts", "priced_hits",
                 "sum_qty", "qty_counts", "start_ms", "last_ms")

    def __init__(self, qty, ts, price=None, ts_ms=None):
        self.qty_variants = {qty}
        self.count = 1
        self.start_ts = ts
        self.last_ts = ts
        self.start_ms = ts_ms
        self.last_ms = ts_ms
        self.last_interval = None
        self.base_interval = None
        self.intervals = []
        self.warned = False
        self.first_price = price
        self.last_price = price
        self.price_counts = {}
        if price is not None:
            self.price_counts[price] = 1
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
        if self.min_interval is None:
            self.min_interval = 1.0
        self.max_interval = settings.get("max_interval")
        if self.max_interval is None:
            self.max_interval = 600
        self.max_qty_variants = settings.get("max_qty_variants", 2)
        self.max_qty_ratio = settings.get("max_qty_ratio", 1.10)
        self.interval_tolerance = settings.get("interval_tolerance")
        self.ignore_qty = settings.get("ignore_qty", False)
        self.time_window_sec = settings.get("time_window_sec", 0.0)
        self.close_after_misses = settings.get("close_after_misses", 6)
        self.max_series = settings.get("max_series", 100000)
        self.short_interval_tolerance = settings.get("short_interval_tolerance", 0.12)
        self.short_interval_threshold = settings.get("short_interval_threshold", 10.0)
        self.long_interval_threshold = settings.get("long_interval_threshold", 120.0)
        self.interval_mult_max = settings.get("interval_mult_max", 4)
        self.stable_qty_required = settings.get("stable_qty_required", False)
        self.stable_qty_ratio = settings.get("stable_qty_ratio", 0.8)
        self.min_display_repeats = settings.get("min_display_repeats", 2)
        # v8: фильтр джиттера. 0 = выключен.
        self.jitter_ratio_max = settings.get("jitter_ratio_max", 0.3)
        # v9: grid lock после подтверждения.
        self.grid_lock = settings.get("grid_lock", True)
        self.grid_tolerance_ms = settings.get("grid_tolerance_ms", 700)
        # v10: фильтр двойных ударов (burst-sell, шум с gap<1с).
        # v10.1: порог уменьшен с 2.0 до 1.0с (было слишком агрессивно).
        self.min_double_hit_gap_sec = settings.get("min_double_hit_gap_sec", 1.0)
        # v10.4: лог всех сделок только по флагу log_all_trades (иначе detector.log растёт лавинообразно)
        self.log_all_trades = settings.get("log_all_trades", False)
        preset_name = settings.get("preset_name")
        self.preset_name = preset_name or ""
        if preset_name:
            self.name = f"робот-интервал[{preset_name}]"
        self.active = {}
        self.index = {}
        self._confirms = []
        self._history_path = Path(__file__).resolve().parent.parent / "data" / "robots_history.jsonl"
        self._history_path.parent.mkdir(exist_ok=True)
        _log.info(f"[{symbol}] INIT: min_qty={self.min_qty}, min_repeats={self.min_repeats}, "
                  f"short_tol={self.short_interval_tolerance}<{self.short_interval_threshold}s, "
                  f"mult_max={self.interval_mult_max}, stable_qty={self.stable_qty_required}, "
                  f"min_display={self.min_display_repeats}, jitter_max={self.jitter_ratio_max}, "
                  f"grid_lock={self.grid_lock}, grid_tol_ms={self.grid_tolerance_ms}, "
                  f"min_double_hit_gap={self.min_double_hit_gap_sec}s")

    def drain_confirms(self):
        out = self._confirms
        self._confirms = []
        return out

    def _write_history(self, side, candidate):
        try:
            record = {
                "timestamp": datetime.now(MSK).isoformat(timespec="seconds"),
                "symbol": self.symbol, "side": side,
                "qty_variants": sorted(candidate.qty_variants),
                "interval_avg": round((candidate.last_ts - candidate.start_ts) / max(candidate.count - 1, 1), 2),
                "repeats": candidate.count,
                "start_ts": candidate.start_ts, "end_ts": candidate.last_ts,
                # v10.2: натуральные мс, напрямую из ленты QUIK (без конверсий у потребителей)
                "interval_ms": round((candidate.last_ms - candidate.start_ms) / max(candidate.count - 1, 1), 1),
                "start_ms": candidate.start_ms, "end_ms": candidate.last_ms,
                "jitter_ms": round(statistics.pstdev(candidate.intervals) * 1000, 1) if len(candidate.intervals) >= 2 else None,
                "price_first": candidate.first_price, "price_last": candidate.last_price,
                "preset": self.preset_name,
            }
            with open(self._history_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            _log.warning(f"[{self.symbol}] history write failed: {e}")

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

    def _should_report(self, signal):
        """v8: фильтр джиттера. Серия с джиттером > jitter_ratio_max × интервал
        считается мусором и не репортится. 0 = фильтр выключен."""
        if self.jitter_ratio_max <= 0:
            return True
        if signal.jitter_ms is None or signal.interval_avg <= 0:
            return True
        return signal.jitter_ms / (signal.interval_avg * 1000) <= self.jitter_ratio_max

    def _register(self, side, candidate):
        self.active.setdefault(side, []).append(candidate)
        if self.ignore_qty:
            return
        si = self.index.setdefault(side, {})
        for q in candidate.qty_variants:
            si.setdefault(q, []).append(candidate)

    def _unregister(self, side, candidate):
        al = self.active.get(side)
        if al:
            try:
                al.remove(candidate)
            except ValueError:
                pass
        if self.ignore_qty:
            return
        si = self.index.get(side, {})
        for q in candidate.qty_variants:
            b = si.get(q)
            if b:
                try:
                    b.remove(candidate)
                except ValueError:
                    pass
                if not b:
                    del si[q]

    def _index_new_variant(self, side, candidate, qty):
        if self.ignore_qty:
            return
        self.index.setdefault(side, {}).setdefault(qty, []).append(candidate)

    def _prune_dead(self, side, now_ts):
        signals = []
        close_threshold = self.max_interval * self.close_after_misses
        al = self.active.get(side, [])
        while al and now_ts - al[0].last_ts > close_threshold:
            c = al.pop(0)
            if c.count >= self.min_repeats:
                sig = self._finalize(side, c)
                if self._should_report(sig):
                    signals.append(sig)
            self._unregister(side, c)
        return signals

    def _enforce_cap(self, side):
        al = self.active.get(side, [])
        overflow = len(al) - self.MAX_ACTIVE_PER_SIDE
        if overflow <= 0:
            return []
        to_evict = sorted(al, key=lambda c: (c.count, c.start_ts))[:overflow]
        signals = []
        for c in to_evict:
            if c.count >= self.min_repeats:
                sig = self._finalize(side, c)
                if self._should_report(sig):
                    signals.append(sig)
            self._unregister(side, c)
        return signals

    def _qty_fits_loose(self, candidate, qty):
        if self.max_qty_ratio is None:
            return True
        low = min(*candidate.qty_variants, qty)
        high = max(*candidate.qty_variants, qty)
        return high / low <= self.max_qty_ratio

    def _qty_stable_for_long(self, candidate):
        if not self.stable_qty_required:
            return True
        if not candidate.qty_counts:
            return False
        total = sum(candidate.qty_counts.values())
        if total < 3:
            return True
        return max(candidate.qty_counts.values()) / total >= self.stable_qty_ratio

    def _interval_fits(self, candidate, interval):
        """Возвращает (ok, base).
        v7: кратные интервалы (k=2..mult_max) до подтверждения.
        v9: grid lock после подтверждения — удар у любого тика k*base
        в окне ±grid_tolerance_ms; пропуски не рвут серию."""
        if interval is None:
            return False, None
        if self.min_interval is None:
            return False, None
        if self.max_interval is None:
            return False, None
        if interval < self.min_interval or interval > self.max_interval:
            return False, None
        last = candidate.last_interval
        if last is None:
            return True, interval
        base = candidate.base_interval or last
        if base <= 0:
            return True, last
        if base < self.short_interval_threshold:
            tol = self.short_interval_tolerance
        else:
            tol = self.interval_tolerance
        if tol is None:
            return True, base
        # k=1: обычный шаг
        if abs(interval - base) <= base * tol:
            return True, base
        if base >= self.short_interval_threshold:
            # v9: grid lock после подтверждения
            if self.grid_lock and candidate.count >= self.min_repeats:
                k = round(interval / base)
                kmax = int(self.max_interval // base)
                if 1 <= k <= kmax and abs(interval - k * base) <= self.grid_tolerance_ms / 1000.0:
                    return True, base
            # k>=2: пропущен удар (до подтверждения)
            k = 2
            while k <= self.interval_mult_max and k * base <= self.max_interval:
                if abs(interval - k * base) <= k * base * tol:
                    return True, base
                k += 1
            # база сама была кратной — уточняем вниз
            k = 2
            while k <= self.interval_mult_max and k * interval <= self.max_interval:
                if abs(base - k * interval) <= k * interval * tol:
                    return True, interval
                k += 1
        return False, None

    def _find_match(self, side, qty, ts):
        if not self.ignore_qty:
            for c in self.index.get(side, {}).get(qty, []):
                ok, _base = self._interval_fits(c, ts - c.last_ts)
                if ok:
                    return c
        for c in reversed(self.active.get(side, [])):
            iv = ts - c.last_ts
            if iv > self.max_interval:
                break
            ok, _base = self._interval_fits(c, iv)
            if not ok:
                continue
            if iv >= self.long_interval_threshold and not self._qty_stable_for_long(c):
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
        if price is None:
            return
        candidate.last_price = price
        candidate.priced_hits += 1
        candidate.price_counts[price] = candidate.price_counts.get(price, 0) + 1

    def _metro(self, candidate):
        if not candidate.intervals:
            return []
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
        side = trade["side"]
        ts = trade["timestamp"] / 1000.0
        ts_ms = trade["timestamp"]  # v10.2: натуральные мс из ленты
        price = trade.get("price")
        
        # v10.3: логирование всех сделок (и ниже min_qty) для анализа FN
        passed_min_qty = qty >= self.min_qty
        if self.log_all_trades:
            _log.info(f"[{self.symbol}] TRADE: qty={qty}, side={side}, ts={ts:.3f}, price={price}, passed_min_qty={passed_min_qty}, min_qty={self.min_qty}")
        
        if not passed_min_qty:
            return []
        
        signals = self._prune_dead(side, ts)
        match = self._find_match(side, qty, ts)
        if match is not None:
            iv = ts - match.last_ts
            # v10: фильтр двойных ударов (burst-sell, шум с gap<1с)
            # v10.1: порог 1.0с (было 2.0с, но TP упал)
            if iv < self.min_double_hit_gap_sec:
                _log.info(f"[{self.symbol}] DOUBLE_HIT_SKIP: side={side}, qty={qty}, "
                          f"interval={iv:.2f}s < {self.min_double_hit_gap_sec}s (noise)")
                return signals
            ok, base = self._interval_fits(match, iv)
            if ok and base is not None:
                match.base_interval = base
            match.intervals.append(iv)
            match.last_interval = iv
            if qty not in match.qty_variants:
                match.qty_variants.add(qty)
                self._index_new_variant(side, match, qty)
            match.count += 1
            match.last_ts = ts
            match.last_ms = ts_ms  # v10.2
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
                self._confirms.append(ts)
                self._write_history(side, match)
                _log.info(f"[{self.symbol}] *** CONFIRMED ***: side={side}, repeats={match.count}, "
                          f"interval={match.last_interval:.2f}s, variants={sorted(match.qty_variants)}")
            if match.count >= self.max_series:
                sig = self._finalize(side, match)
                if self._should_report(sig):
                    signals.append(sig)
                self._unregister(side, match)
        else:
            new_c = Candidate(qty, ts, price, ts_ms)
            self._register(side, new_c)
            _log.info(f"[{self.symbol}] NEW: side={side}, qty={qty}, price={price}")
            signals.extend(self._enforce_cap(side))
        return signals

    def check_overdue(self, now_ts):
        signals, warnings = [], []
        close_threshold = self.max_interval * self.close_after_misses
        for side, cands in list(self.active.items()):
            for c in list(cands):
                gap = now_ts - c.last_ts
                if gap > close_threshold:
                    if c.count >= self.min_repeats:
                        sig = self._finalize(side, c)
                        if self._should_report(sig):
                            signals.append(sig)
                    self._unregister(side, c)
                    continue
                if c.count < self.min_repeats:
                    continue
                base = c.base_interval or c.last_interval
                if base is not None:
                    tol = self.short_interval_tolerance if base < self.short_interval_threshold else self.interval_tolerance
                    if tol is not None:
                        max_fit = base * (1 + tol)
                        if gap > max_fit and not c.warned:
                            c.warned = True
                            warnings.append({"symbol": self.symbol, "side": side,
                                           "qty_variants": sorted(c.qty_variants),
                                           "gap_sec": round(gap, 1), "max_fit_sec": round(max_fit, 1),
                                           "просрочка": f"{gap:.1f}s > {max_fit:.1f}s"})
                            _log.info(f"[{self.symbol}] OVERDUE: gap={gap:.1f}s > {max_fit:.1f}s")
        return signals, warnings

    def get_active_snapshot(self, now_ts):
        rows = []
        for side, cands in self.active.items():
            for c in cands:
                if c.count < self.min_display_repeats:
                    continue
                base = c.base_interval or c.last_interval
                seconds_to_next = (c.last_ts + base - now_ts) if base is not None else None
                jitter_ms = statistics.pstdev(c.intervals) * 1000 if len(c.intervals) >= 2 else None
                # v8: фильтр джиттера — мусорные серии не показываем в снапшоте
                if self.jitter_ratio_max > 0 and jitter_ms is not None and base is not None:
                    if jitter_ms / (base * 1000) > self.jitter_ratio_max:
                        continue
                same_price_ratio = None
                if c.priced_hits > 0 and c.price_counts:
                    same_price_ratio = max(c.price_counts.values()) / c.priced_hits
                price_shift = None
                if c.first_price is not None and c.last_price is not None:
                    price_shift = c.last_price - c.first_price
                rows.append({"symbol": self.symbol, "preset": self.preset_name, "side": side,
                             "qty_variants": sorted(c.qty_variants), "interval": base,
                             "repeats": c.count, "seconds_to_next": seconds_to_next,
                             "start_ts": c.start_ts, "jitter_ms": jitter_ms,
                             "price_first": c.first_price, "price_last": c.last_price,
                             "price_shift": price_shift, "same_price_ratio": same_price_ratio,
                             "sum_qty": c.sum_qty, "metro": self._metro(c)})
        return rows

    def flush(self):
        signals = []
        for side, cands in list(self.active.items()):
            for c in cands:
                if c.count >= self.min_repeats:
                    sig = self._finalize(side, c)
                    if self._should_report(sig):
                        signals.append(sig)
        self.active.clear()
        self.index.clear()
        return signals