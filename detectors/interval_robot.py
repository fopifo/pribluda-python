"""
Приблуда на python — детектор периодичности (роботов), персистентный.
Лог: output/detector.log. MAX_ACTIVE_PER_SIDE=500.
Механизмы v5-v13.4: история при подтверждении и закрытии; кратные k*база=
пропуск; фильтр двойных ударов; адаптивный min_qty; noisy-qty блок;
перещёлкивание базы; metro для GUI; период=медиана зазоров; k-кратные и
для коротких баз; max_qty_variants=4; report_min_repeats=8; grid-джиттер;
regularity-гейт report_min_regular=0.6.
v13.4: ПЕРЕОЦЕНКА ПЕРИОДА ПО СЫРОЙ ЛЕНТЕ. Скользящий буфер всех сделок
стороны (tape_buf_max). При репорте: сырые зазоры между ударами своего qty
в окне серии; если есть более мелкая регулярная сетка (reg>=0.6), а
принятый период = её кратное (k>=2) -> репортим истинный период.
Чинит base-multiple (SNGSP true 5s -> репорт 30s) без per-ticker.
"""
import json
import logging
import statistics
import os
from collections import deque
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

RELOCK_TOL_MS = 150.0


class Candidate:
    __slots__ = ("qty_variants", "count", "start_ts", "last_ts",
                 "last_interval", "base_interval", "intervals", "warned",
                 "first_price", "last_price", "price_counts", "priced_hits",
                 "sum_qty", "qty_counts", "start_ms", "last_ms",
                 "intervals_ms", "grid_single", "grid_multi", "cb_block", "side")

    def __init__(self, qty, ts, price=None, ts_ms=None, side=None):
        self.qty_variants = {qty}
        self.count = 1
        self.start_ts = ts
        self.last_ts = ts
        self.start_ms = ts_ms
        self.last_ms = ts_ms
        self.last_interval = None
        self.base_interval = None
        self.intervals = []
        self.intervals_ms = []
        self.warned = False
        self.first_price = price
        self.last_price = price
        self.price_counts = {}
        if price is not None:
            self.price_counts[price] = 1
        self.priced_hits = 1 if price is not None else 0
        self.sum_qty = qty
        self.qty_counts = {qty: 1}
        self.grid_single = 0
        self.grid_multi = 0
        self.cb_block = False
        self.side = side


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
        self.max_qty_variants = settings.get("max_qty_variants", 4)
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
        self.jitter_ratio_max = settings.get("jitter_ratio_max", 0.3)
        self.grid_lock = settings.get("grid_lock", True)
        self.grid_tolerance_ms = settings.get("grid_tolerance_ms", 700)
        self.min_double_hit_gap_sec = settings.get("min_double_hit_gap_sec", 1.0)
        self.log_all_trades = settings.get("log_all_trades", False)
        self.min_qty_auto = settings.get("min_qty_auto", False)
        self.min_qty_median_pct = settings.get("min_qty_median_pct", 0.5)
        self._adaptive_qty_computed = False
        self.noisy_qty_filter = settings.get("noisy_qty_filter", False)
        self.noisy_qty_share = settings.get("noisy_qty_share", 0.03)
        self._qty_freq = {}
        self._qty_total = 0
        self.report_min_repeats = settings.get("report_min_repeats", 8)
        self.report_min_regular = settings.get("report_min_regular", 0.6)
        self.tape_buf_max = settings.get("tape_buf_max", 4000)
        self._tape_buf = {}
        preset_name = settings.get("preset_name")
        self.preset_name = preset_name or ""
        if preset_name:
            self.name = f"робот-интервал[{preset_name}]"
        self.active = {}
        self.index = {}
        self._confirms = []
        self._history_path = Path(__file__).resolve().parent.parent / "data" / "robots_history.jsonl"
        self._history_path.parent.mkdir(exist_ok=True)
        _log.info(f"[{symbol}] INIT (v13.4): min_qty={self.min_qty}, min_repeats={self.min_repeats}, "
                  f"report_min_repeats={self.report_min_repeats}, report_min_regular={self.report_min_regular}, "
                  f"tape_buf_max={self.tape_buf_max}, mult_max={self.interval_mult_max}, "
                  f"jitter_max={self.jitter_ratio_max}, grid_tol_ms={self.grid_tolerance_ms}, "
                  f"max_qty_variants={self.max_qty_variants}")

    def _compute_adaptive_min_qty(self, ts_ms):
        try:
            date_str = datetime.fromtimestamp(ts_ms / 1000, tz=MSK).strftime("%Y-%m-%d")
            data_dir = Path(__file__).resolve().parent.parent / "data"
            alor_file = data_dir / f"{self.symbol}_{date_str}.json"
            if not alor_file.exists():
                return 1
            with open(alor_file, encoding="utf-8") as f:
                trades = json.load(f)
            if not trades:
                return 1
            qtys = [float(t.get("qty", 0)) for t in trades if t.get("qty")]
            if not qtys:
                return 1
            median_qty = statistics.median(qtys)
            adaptive_qty = int(median_qty * self.min_qty_median_pct)
            return adaptive_qty
        except Exception as e:
            _log.warning(f"[{self.symbol}] ADAPTIVE_QTY failed: {e}, дефолт=1")
            return 1

    def drain_confirms(self):
        out = self._confirms
        self._confirms = []
        return out

    def _noisy_qtys(self):
        if not self.noisy_qty_filter or self._qty_total < 100:
            return set()
        return {q for q, n in self._qty_freq.items()
                if n / self._qty_total >= self.noisy_qty_share}

    def _cb_update(self, candidate, iv, base):
        k = round(iv / base) if base > 0 else 1
        if k <= 1:
            candidate.grid_single += 1
        else:
            candidate.grid_multi += 1
        s = candidate.grid_single
        m = candidate.grid_multi
        total = s + m
        blocked = False
        if total > 0 and m > s:
            noisy = self._noisy_qtys()
            if noisy:
                if candidate.qty_counts:
                    primary_qty = max(candidate.qty_counts, key=candidate.qty_counts.get)
                else:
                    primary_qty = next(iter(candidate.qty_variants), None)
                if primary_qty is not None and primary_qty in noisy:
                    blocked = (s / total) < 0.8
        candidate.cb_block = blocked

    def _jitter_ms(self, candidate):
        if len(candidate.intervals_ms) >= 2:
            return statistics.pstdev(candidate.intervals_ms)
        return None

    def _grid_jitter_ms(self, candidate):
        base = candidate.base_interval or candidate.last_interval
        if not base or base <= 0 or len(candidate.intervals_ms) < 2:
            return None
        base_ms = base * 1000.0
        devs = [abs(iv - round(iv / base_ms) * base_ms) for iv in candidate.intervals_ms]
        return statistics.pstdev(devs)

    def _raw_period(self, candidate):
        """v13.4: истинный период по СЫРЫМ зазорам своего qty в окне серии."""
        buf = self._tape_buf.get(candidate.side)
        if not buf or candidate.start_ms is None or candidate.last_ms is None:
            return None
        hits = [t for (t, q) in buf
                if q in candidate.qty_variants
                and candidate.start_ms <= t <= candidate.last_ms]
        if len(hits) < 8:
            return None
        hits.sort()
        gaps = [hits[i+1] - hits[i] for i in range(len(hits) - 1)]
        med = statistics.median(gaps)
        if med <= 0:
            return None
        tol = max(2000.0, 0.2 * med)
        good = sum(1 for g in gaps if abs(g - round(g / med) * med) <= tol)
        if good / len(gaps) < 0.6:
            return None
        return med / 1000.0

    def _median_interval(self, candidate):
        if len(candidate.intervals) >= 3:
            acc = statistics.median(candidate.intervals)
        else:
            acc = (candidate.last_ts - candidate.start_ts) / max(candidate.count - 1, 1)
        raw = self._raw_period(candidate)
        if raw is not None and acc > 0 and raw < acc * 0.8:
            k = round(acc / raw)
            if k >= 2 and abs(acc - k * raw) <= max(2.0, 0.2 * acc):
                return raw
        return acc

    def _regularity(self, candidate):
        ivs = candidate.intervals_ms
        if len(ivs) < 3:
            return 1.0
        med = statistics.median(ivs)
        if med <= 0:
            return 1.0
        tol = max(2000.0, 0.2 * med)
        good = sum(1 for iv in ivs if abs(iv - round(iv / med) * med) <= tol)
        return good / len(ivs)

    def _report_ok(self, candidate):
        if self.report_min_regular > 0 and self._regularity(candidate) < self.report_min_regular:
            return False
        if self.jitter_ratio_max > 0:
            gj = self._grid_jitter_ms(candidate)
            iv = self._median_interval(candidate)
            if gj is not None and iv > 0 and gj / (iv * 1000) > self.jitter_ratio_max:
                return False
        return True

    def _write_history(self, side, candidate):
        try:
            jitter = self._jitter_ms(candidate)
            record = {
                "timestamp": datetime.now(MSK).isoformat(timespec="seconds"),
                "symbol": self.symbol, "side": side,
                "qty_variants": sorted(candidate.qty_variants),
                "interval_avg": round(self._median_interval(candidate), 2),
                "repeats": candidate.count,
                "start_ts": candidate.start_ts, "end_ts": candidate.last_ts,
                "interval_ms": round((candidate.last_ms - candidate.start_ms) / max(candidate.count - 1, 1), 1),
                "start_ms": candidate.start_ms, "end_ms": candidate.last_ms,
                "jitter_ms": round(jitter, 1) if jitter is not None else None,
                "price_first": candidate.first_price, "price_last": candidate.last_price,
                "preset": self.preset_name,
            }
            with open(self._history_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            _log.warning(f"[{self.symbol}] history write failed: {e}")

    def _finalize(self, side, candidate):
        avg_interval = self._median_interval(candidate)
        jitter_ms = self._jitter_ms(candidate)
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

    def _close_series(self, side, c):
        signals = []
        if c.count >= self.report_min_repeats and not c.cb_block:
            if self._report_ok(c):
                signals.append(self._finalize(side, c))
                self._write_history(side, c)
        self._unregister(side, c)
        return signals

    def _prune_dead(self, side, now_ts):
        signals = []
        close_threshold = self.max_interval * self.close_after_misses
        al = self.active.get(side, [])
        while al and now_ts - al[0].last_ts > close_threshold:
            c = al.pop(0)
            signals.extend(self._close_series(side, c))
        return signals

    def _enforce_cap(self, side):
        al = self.active.get(side, [])
        overflow = len(al) - self.MAX_ACTIVE_PER_SIDE
        if overflow <= 0:
            return []
        to_evict = sorted(al, key=lambda c: (c.count, c.start_ts))[:overflow]
        signals = []
        for c in to_evict:
            signals.extend(self._close_series(side, c))
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
        if abs(interval - base) <= base * tol:
            return True, base
        if self.grid_lock and candidate.count >= self.min_repeats:
            k = round(interval / base)
            kmax = int(self.max_interval // base)
            if 1 <= k <= kmax and abs(interval - k * base) <= self.grid_tolerance_ms / 1000.0:
                return True, base
        k = 2
        while k <= self.interval_mult_max and k * base <= self.max_interval:
            if abs(interval - k * base) <= k * base * tol:
                return True, base
            k += 1
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

    def _relock_base(self, candidate):
        if len(candidate.intervals_ms) < 3:
            return
        base = candidate.base_interval or candidate.last_interval
        if not base or base <= 0:
            return
        base_ms = base * 1000.0
        med_ms = statistics.median(candidate.intervals_ms)
        if base > self.long_interval_threshold and med_ms < self.short_interval_threshold * 1000:
            short_threshold_ms = self.short_interval_threshold * 1000.0
            short_count = sum(1 for iv in candidate.intervals_ms if iv < short_threshold_ms)
            if short_count >= 0.8 * len(candidate.intervals_ms):
                short_stable = sum(1 for iv in candidate.intervals_ms
                                   if iv < short_threshold_ms and abs(iv - med_ms) <= RELOCK_TOL_MS)
                if short_stable >= 0.8 * short_count:
                    candidate.base_interval = med_ms / 1000.0
                    return
        if abs(med_ms - base_ms) <= RELOCK_TOL_MS:
            return
        good = sum(1 for iv in candidate.intervals_ms
                   if abs(iv - med_ms) <= RELOCK_TOL_MS)
        if good >= 0.8 * len(candidate.intervals_ms):
            candidate.base_interval = med_ms / 1000.0

    def _refine_base(self, candidate):
        base = candidate.base_interval or candidate.last_interval
        if base is None or len(candidate.intervals) < 4:
            return
        if base < self.short_interval_threshold:
            return
        for k in (2, 3, 4):
            d = base / k
            if d < 2.0 or d > self.max_interval:
                continue
            tol = self.short_interval_tolerance if d < self.short_interval_threshold else (self.interval_tolerance or 0.1)
            gtol = max(d * tol, self.grid_tolerance_ms / 1000.0)
            good = 0; single = False
            for iv in candidate.intervals:
                m = round(iv / d)
                if m >= 1 and abs(iv - m * d) <= max(m * d * tol, self.grid_tolerance_ms / 1000.0):
                    good += 1
                    if m == 1 and abs(iv - d) <= gtol:
                        single = True
            if single and good >= 0.8 * len(candidate.intervals):
                candidate.base_interval = d
                return

    def _metro(self, candidate):
        if not candidate.intervals_ms:
            return []
        base = candidate.base_interval or candidate.last_interval
        if not base or base <= 0:
            return []
        base_ms = base * 1000.0
        out = []
        for iv_ms in candidate.intervals_ms[-3:]:
            dev = abs(iv_ms - round(iv_ms / base_ms) * base_ms)
            dev = int(round(dev))
            st = "ok" if dev <= 150 else ("warn" if dev <= 300 else "bad")
            out.append((dev, st))
        return out

    def on_trade(self, trade):
        qty = trade["qty"]
        side = trade["side"]
        ts = trade["timestamp"] / 1000.0
        ts_ms = trade["timestamp"]
        price = trade.get("price")

        if self.noisy_qty_filter:
            self._qty_freq[qty] = self._qty_freq.get(qty, 0) + 1
            self._qty_total += 1

        if self.min_qty_auto and not self._adaptive_qty_computed:
            adaptive_qty = self._compute_adaptive_min_qty(ts_ms)
            self.min_qty = max(adaptive_qty, 1)
            self._adaptive_qty_computed = True

        passed_min_qty = qty >= self.min_qty
        if self.log_all_trades:
            _log.info(f"[{self.symbol}] TRADE: qty={qty}, side={side}, ts={ts:.3f}, passed_min_qty={passed_min_qty}")
        if not passed_min_qty:
            return []

        # v13.4: скользящий буфер сырой ленты стороны
        buf = self._tape_buf.get(side)
        if buf is None:
            buf = deque(maxlen=self.tape_buf_max)
            self._tape_buf[side] = buf
        buf.append((ts_ms, qty))

        signals = self._prune_dead(side, ts)
        match = self._find_match(side, qty, ts)
        if match is not None:
            iv = ts - match.last_ts
            if iv < self.min_double_hit_gap_sec:
                return signals
            ok, base = self._interval_fits(match, iv)
            if ok and base is not None:
                match.base_interval = base
                if self.noisy_qty_filter and base >= self.short_interval_threshold:
                    self._cb_update(match, iv, base)
            match.intervals.append(iv)
            if ts_ms is not None and match.last_ms is not None:
                match.intervals_ms.append(ts_ms - match.last_ms)
            self._relock_base(match)
            self._refine_base(match)
            match.last_interval = iv
            if qty not in match.qty_variants:
                match.qty_variants.add(qty)
                self._index_new_variant(side, match, qty)
            match.count += 1
            match.last_ts = ts
            match.last_ms = ts_ms
            match.warned = False
            self._apply_price(match, qty, price)
            al = self.active.get(side, [])
            try:
                if al[-1] is not match:
                    al.remove(match)
                    al.append(match)
            except ValueError:
                al.append(match)
            if match.count == self.report_min_repeats and not match.cb_block:
                self._confirms.append(ts)
                if self._report_ok(match):
                    self._write_history(side, match)
            if match.count >= self.max_series:
                signals.extend(self._close_series(side, match))
        else:
            new_c = Candidate(qty, ts, price, ts_ms, side)
            self._register(side, new_c)
            signals.extend(self._enforce_cap(side))
        return signals

    def check_overdue(self, now_ts):
        signals, warnings = [], []
        close_threshold = self.max_interval * self.close_after_misses
        for side, cands in list(self.active.items()):
            for c in list(cands):
                gap = now_ts - c.last_ts
                if gap > close_threshold:
                    signals.extend(self._close_series(side, c))
                    continue
                if c.count < self.min_repeats:
                    continue
                if c.cb_block:
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
        return signals, warnings

    def get_active_snapshot(self, now_ts):
        rows = []
        for side, cands in self.active.items():
            for c in cands:
                if c.count < self.min_display_repeats:
                    continue
                if c.cb_block:
                    continue
                base = c.base_interval or c.last_interval
                seconds_to_next = (c.last_ts + base - now_ts) if base is not None else None
                jitter_ms = self._jitter_ms(c)
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
                signals.extend(self._close_series(side, c))
        self.active.clear()
        self.index.clear()
        return signals