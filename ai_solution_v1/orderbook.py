"""
ai_solution_v1/orderbook.py
===========================
Детектор аномалий в стакане (orderbook): «мигалки» (spoofing) и «стены».
Только чтение JSONL-файла стакана и генерация событий.
"""
from __future__ import annotations

import json
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, TextIO, Tuple

__all__ = [
    "Side", "OrderbookLevel", "SpoofEvent", "WallEvent", "OrderbookConfig",
    "OrderbookState", "OrderbookDetector", "OrderbookReport",
    "iter_snapshots", "run_orderbook", "format_orderbook_report",
]

SIDE_BID = 1
SIDE_ASK = -1
SIDE_NAMES = {SIDE_BID: "bid", SIDE_ASK: "ask"}
_PRICE_SCALE = 1_000_000

def _price_key(price: float) -> int:
    return int(round(price * _PRICE_SCALE))

def _price_from_raw(raw: List[Tuple[float, int]], key: int) -> float:
    for p, _q in raw:
        if _price_key(p) == key:
            return p
    return key / _PRICE_SCALE

@dataclass(slots=True)
class OrderbookConfig:
    spoof_qty_factor: float = 5.0
    spoof_min_qty: int = 100
    spoof_ttl_ms: int = 5_000
    ema_warmup: int = 30
    ema_alpha: float = 0.02
    spoof_cooldown_ms: int = 5_000
    wall_hold_ms: int = 30_000
    wall_qty_factor: float = 5.0
    wall_min_qty: int = 100
    wall_cooldown_ms: int = 0
    max_levels: int = 0
    min_track_qty: int = 1
    gap_reset_ms: int = 60_000

    def validate(self) -> None:
        if self.spoof_ttl_ms <= 0: raise ValueError("spoof_ttl_ms > 0")
        if self.wall_hold_ms <= 0: raise ValueError("wall_hold_ms > 0")
        if self.spoof_qty_factor < 1.0 or self.wall_qty_factor < 1.0: raise ValueError("qty_factor >= 1.0")
        if not (0.0 < self.ema_alpha <= 1.0): raise ValueError("ema_alpha in (0, 1]")

@dataclass(slots=True)
class OrderbookLevel:
    side: int
    price: float
    qty: int
    ts_ms: int
    def to_dict(self) -> Dict[str, Any]:
        return {"side": SIDE_NAMES[self.side], "price": self.price, "qty": self.qty, "ts_ms": self.ts_ms}

@dataclass(slots=True)
class SpoofEvent:
    kind: str
    symbol: str
    side: int
    price: float
    qty: int
    ts_appeared_ms: int
    ts_disappeared_ms: int
    lifetime_ms: int
    ema_at_level: float
    threshold: int
    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "symbol": self.symbol, "side": SIDE_NAMES[self.side], "price": self.price,
                "qty": self.qty, "ts_appeared_ms": self.ts_appeared_ms, "ts_disappeared_ms": self.ts_disappeared_ms,
                "lifetime_ms": self.lifetime_ms, "ema_at_level": round(self.ema_at_level, 2), "threshold": self.threshold}
    def __str__(self) -> str:
        return f"[spoof] {self.symbol} {SIDE_NAMES[self.side]} {self.price} qty={self.qty} жила {self.lifetime_ms} мс"

@dataclass(slots=True)
class WallEvent:
    kind: str
    symbol: str
    side: int
    price: float
    qty: int
    ts_appeared_ms: int
    ts_ms: int
    held_ms: int
    ema_at_level: float
    threshold: int
    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "symbol": self.symbol, "side": SIDE_NAMES[self.side], "price": self.price,
                "qty": self.qty, "ts_appeared_ms": self.ts_appeared_ms, "ts_ms": self.ts_ms,
                "held_ms": self.held_ms, "ema_at_level": round(self.ema_at_level, 2), "threshold": self.threshold}
    def __str__(self) -> str:
        return f"[wall] {self.symbol} {SIDE_NAMES[self.side]} {self.price} qty={self.qty} стоит {self.held_ms} мс"

@dataclass(slots=True)
class Snapshot:
    symbol: str
    ts_ms: int
    bids: Dict[int, int]
    asks: Dict[int, int]
    raw_bids: List[Tuple[float, int]] = field(default_factory=list)
    raw_asks: List[Tuple[float, int]] = field(default_factory=list)

def _parse_side(rows: Any, max_levels: int) -> Tuple[Dict[int, int], List[Tuple[float, int]]]:
    out: Dict[int, int] = {}
    raw: List[Tuple[float, int]] = []
    if not isinstance(rows, (list, tuple)): return out, raw
    n = 0
    for row in rows:
        try: price = float(row[0]); qty = int(float(row[1]))
        except (TypeError, ValueError, IndexError): continue
        if qty <= 0: continue
        key = _price_key(price)
        out[key] = out.get(key, 0) + qty
        raw.append((price, qty))
        n += 1
        if max_levels and n >= max_levels: break
    return out, raw

def parse_snapshot(raw: Mapping[str, Any], max_levels: int = 0) -> Optional[Snapshot]:
    symbol = raw.get("symbol")
    if not symbol or not isinstance(symbol, str): return None
    try: ts_ms = int(raw.get("timestamp"))
    except (TypeError, ValueError): return None
    if ts_ms <= 0: return None
    bids, raw_bids = _parse_side(raw.get("bids") or raw.get("b"), max_levels)
    asks, raw_asks = _parse_side(raw.get("asks") or raw.get("a"), max_levels)
    if not bids and not asks: return None
    return Snapshot(symbol=symbol, ts_ms=ts_ms, bids=bids, asks=asks, raw_bids=raw_bids, raw_asks=raw_asks)

def iter_snapshots(path: Path, max_levels: int = 0) -> Iterator[Snapshot]:
    with open(path, "r", encoding="utf-8", errors="replace", newline=None) as fh:
        for line in fh:
            if not line or not line.strip(): continue
            try: d = json.loads(line)
            except (ValueError, TypeError): continue
            if not isinstance(d, dict): continue
            snap = parse_snapshot(d, max_levels)
            if snap is not None: yield snap

class _Level:
    __slots__ = ("side", "price", "price_key", "qty_max", "first_ms", "last_ms", "ema_at_appear", "threshold", "wall_reported")
    def __init__(self, side: int, price: float, price_key: int, qty: int, ts_ms: int, ema: float, threshold: int) -> None:
        self.side = side; self.price = price; self.price_key = price_key; self.qty_max = qty
        self.first_ms = ts_ms; self.last_ms = ts_ms; self.ema_at_appear = ema; self.threshold = threshold; self.wall_reported = False

class OrderbookState:
    __slots__ = ("symbol", "config", "levels", "ema", "samples", "last_ts", "cooldown", "counters")
    def __init__(self, symbol: str, config: OrderbookConfig) -> None:
        self.symbol = symbol; self.config = config; self.levels: Dict[Tuple[int, int], _Level] = {}
        self.ema: float = 0.0; self.samples: int = 0; self.last_ts: int = 0; self.cooldown: Dict[Tuple[int, int], int] = {}
        self.counters = {"spoof": 0, "wall": 0, "levels_appeared": 0, "levels_disappeared": 0, "reset": 0, "snapshots": 0}

    def _update_ema(self, snap: Snapshot) -> None:
        total = 0; n = 0
        for qty in snap.bids.values(): total += qty; n += 1
        for qty in snap.asks.values(): total += qty; n += 1
        if n == 0: return
        mean = total / n; a = self.config.ema_alpha
        self.ema = mean if self.samples == 0 else (1.0 - a) * self.ema + a * mean
        self.samples += 1

    def _threshold(self, side: int, factor: float, min_qty: int) -> int:
        if self.samples < self.config.ema_warmup: return min_qty
        return max(int(self.ema * factor), min_qty)

    def on_snapshot(self, snap: Snapshot) -> List[Any]:
        events: List[Any] = []; ts = snap.ts_ms
        if self.last_ts and ts - self.last_ts > self.config.gap_reset_ms:
            self.levels.clear(); self.cooldown.clear(); self.counters["reset"] += 1
        self.last_ts = ts; self.counters["snapshots"] += 1; self._update_ema(snap)

        for side, book, raw in ((SIDE_BID, snap.bids, snap.raw_bids), (SIDE_ASK, snap.asks, snap.raw_asks)):
            for pk, qty_new in book.items():
                prev = self.levels.get((side, pk))
                if prev is None:
                    if qty_new < self.config.min_track_qty: continue
                    price = _price_from_raw(raw, pk)
                    thr = self._threshold(side, self.config.spoof_qty_factor, self.config.spoof_min_qty)
                    self.levels[(side, pk)] = _Level(side, price, pk, qty_new, ts, self.ema, thr)
                    self.counters["levels_appeared"] += 1
                else:
                    prev.last_ms = ts
                    if qty_new > prev.qty_max:
                        prev.qty_max = qty_new
                        prev.threshold = self._threshold(side, self.config.spoof_qty_factor, self.config.spoof_min_qty)
                        prev.ema_at_appear = self.ema
            
            gone = [key for key in self.levels if key[0] == side and key[1] not in book]
            for key in gone:
                lvl = self.levels.pop(key)
                events.extend(self._on_level_gone(lvl, ts))
        
        events.extend(self._check_walls(ts))
        return events

    def _on_level_gone(self, lvl: _Level, ts: int) -> List[Any]:
        self.counters["levels_disappeared"] += 1
        lifetime = ts - lvl.first_ms
        if lvl.qty_max >= lvl.threshold and 0 <= lifetime <= self.config.spoof_ttl_ms:
            key = (lvl.side, lvl.price_key)
            if ts - self.cooldown.get(key, 0) >= self.config.spoof_cooldown_ms:
                self.cooldown[key] = ts; self.counters["spoof"] += 1
                return [SpoofEvent(kind="spoof", symbol=self.symbol, side=lvl.side, price=lvl.price, qty=lvl.qty_max,
                                   ts_appeared_ms=lvl.first_ms, ts_disappeared_ms=ts, lifetime_ms=lifetime,
                                   ema_at_level=lvl.ema_at_appear, threshold=lvl.threshold)]
        return []

    def _check_walls(self, ts: int) -> List[Any]:
        out: List[Any] = []; thr_wall = self._threshold(SIDE_BID, self.config.wall_qty_factor, self.config.wall_min_qty)
        for lvl in self.levels.values():
            if lvl.wall_reported or ts - lvl.first_ms < self.config.wall_hold_ms: continue
            if lvl.qty_max < max(thr_wall, self.config.wall_min_qty): continue
            lvl.wall_reported = True; self.counters["wall"] += 1
            out.append(WallEvent(kind="wall", symbol=self.symbol, side=lvl.side, price=lvl.price, qty=lvl.qty_max,
                                 ts_appeared_ms=lvl.first_ms, ts_ms=ts, held_ms=ts - lvl.first_ms,
                                 ema_at_level=lvl.ema_at_appear, threshold=max(thr_wall, self.config.wall_min_qty)))
        return out

@dataclass
class OrderbookReport:
    file: str = ""
    elapsed_sec: float = 0.0
    lines_read: int = 0
    lines_bad: int = 0
    snapshots: int = 0
    symbols: int = 0
    spoof: int = 0
    wall: int = 0
    events_per_symbol: Dict[str, int] = field(default_factory=dict)
    stats_per_symbol: Dict[str, Dict[str, int]] = field(default_factory=dict)
    top_spoof: List[Dict[str, Any]] = field(default_factory=list)
    top_wall: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    def to_dict(self) -> Dict[str, Any]:
        return {"file": self.file, "elapsed_sec": round(self.elapsed_sec, 2), "lines_read": self.lines_read,
                "lines_bad": self.lines_bad, "snapshots": self.snapshots, "symbols": self.symbols,
                "spoof": self.spoof, "wall": self.wall, "events_per_symbol": self.events_per_symbol, "warnings": self.warnings}

class OrderbookDetector:
    __slots__ = ("config", "states", "counters")
    def __init__(self, config: Optional[OrderbookConfig] = None) -> None:
        cfg = config or OrderbookConfig(); cfg.validate()
        self.config = cfg; self.states: Dict[str, OrderbookState] = {}
        self.counters = {"spoof": 0, "wall": 0, "snapshots": 0, "events": 0}
    def state(self, symbol: str) -> OrderbookState:
        if symbol not in self.states: self.states[symbol] = OrderbookState(symbol, self.config)
        return self.states[symbol]
    def on_snapshot(self, snap: Snapshot) -> List[Any]:
        st = self.state(snap.symbol); events = st.on_snapshot(snap)
        if events:
            for e in events: self.counters[e.kind] = self.counters.get(e.kind, 0) + 1
            self.counters["events"] += len(events)
        self.counters["snapshots"] += 1; return events
    def on_raw(self, raw: Mapping[str, Any]) -> List[Any]:
        snap = parse_snapshot(raw, self.config.max_levels)
        return self.on_snapshot(snap) if snap else []

def run_orderbook(
    path: Path, *, config: Optional[OrderbookConfig] = None, symbols: Optional[Iterable[str]] = None,
    limit_lines: int = 0, on_event: Optional[Callable[[Any], None]] = None, out: Optional[TextIO] = None
) -> OrderbookReport:
    path = Path(path)
    if not path.exists(): raise FileNotFoundError(f"файл стакана не найден: {path}")
    if path.is_dir(): raise IsADirectoryError(f"это не файл стакана: {path}")

    stdout = out if out is not None else sys.stdout
    is_tty = bool(getattr(stdout, "isatty", lambda: False)())
    det = OrderbookDetector(config); sym_filter = set(symbols) if symbols else None
    report = OrderbookReport(file=str(path))
    spoof_samples: List[Dict[str, Any]] = []; wall_samples: List[Dict[str, Any]] = []
    t0 = time.perf_counter(); last_progress_time = t0; next_progress = 200_000
    total_size = path.stat().st_size; bytes_read = 0

    with open(path, "r", encoding="utf-8", errors="replace", newline=None) as fh:
        for line in fh:
            bytes_read += len(line.encode('utf-8'))
            report.lines_read += 1
            if limit_lines and report.lines_read > limit_lines:
                report.lines_read -= 1; break
            if not line or not line.strip(): continue
            try: raw = json.loads(line)
            except (ValueError, TypeError): report.lines_bad += 1; continue
            if not isinstance(raw, dict): report.lines_bad += 1; continue
            sym = raw.get("symbol")
            if sym_filter is not None and sym not in sym_filter: continue
            snap = parse_snapshot(raw, det.config.max_levels)
            if snap is None: report.lines_bad += 1; continue
            report.snapshots += 1
            for ev in det.on_snapshot(snap):
                if isinstance(ev, SpoofEvent):
                    report.spoof += 1
                    if len(spoof_samples) < 20: spoof_samples.append(ev.to_dict())
                elif isinstance(ev, WallEvent):
                    report.wall += 1
                    if len(wall_samples) < 20: wall_samples.append(ev.to_dict())
                if on_event is not None: on_event(ev)
            
            now = time.perf_counter()
            if is_tty and (now - last_progress_time >= 1.0 or report.lines_read >= next_progress):
                last_progress_time = now
                pct = (bytes_read / total_size * 100) if total_size > 0 else 0
                rate = report.lines_read / max(now - t0, 1e-9)
                stdout.write(f"\r[orderbook] {pct:3.1f}% | {report.lines_read} строк | {rate:.0f} строк/с | spoof={report.spoof} wall={report.wall}")
                stdout.flush()
                next_progress = report.lines_read + 200_000

    if is_tty: stdout.write("\n"); stdout.flush()
    report.elapsed_sec = time.perf_counter() - t0
    report.symbols = len(det.states)
    report.events_per_symbol = {s: st.counters["spoof"] + st.counters["wall"] for s, st in det.states.items() if st.counters["spoof"] or st.counters["wall"]}
    report.stats_per_symbol = {s: dict(st.counters) for s, st in det.states.items()}
    report.top_spoof = sorted(spoof_samples, key=lambda d: -d["qty"])[:10]
    report.top_wall = sorted(wall_samples, key=lambda d: -d["qty"])[:10]
    return report

def format_orderbook_report(report: OrderbookReport) -> str:
    rows = ["=" * 72, "АНАЛИЗ СТАКАНА", "=" * 72,
            f"Файл:            {Path(report.file).name}", f"Время:           {report.elapsed_sec:.1f} с",
            f"Строк:           {report.lines_read:,}".replace(",", " "),
            f"  битых:         {report.lines_bad:,}".replace(",", " "),
            f"Снимков:         {report.snapshots:,}".replace(",", " "),
            f"Тикеров:         {report.symbols}", f"Мигалок (spoof): {report.spoof:,}".replace(",", " "),
            f"Стен (wall):     {report.wall:,}".replace(",", " ")]
    if report.events_per_symbol:
        rows.append(""); rows.append("События по тикерам (топ-15):")
        for sym, n in sorted(report.events_per_symbol.items(), key=lambda kv: -kv[1])[:15]:
            st = report.stats_per_symbol.get(sym, {})
            rows.append(f"  {sym:<14} событий {n:>5}  (spoof {st.get('spoof', 0)}, wall {st.get('wall', 0)})")
    if report.top_spoof:
        rows.append(""); rows.append("Крупнейшие мигалки:")
        rows.append(f"  {'тикер':<12}{'сторона':<6}{'цена':>12}{'qty':>8}{'жила, мс':>10}")
        for d in report.top_spoof[:10]: rows.append(f"  {d['symbol']:<12}{d['side']:<6}{d['price']:>12}{d['qty']:>8}{d['lifetime_ms']:>10}")
    if report.top_wall:
        rows.append(""); rows.append("Крупнейшие стены:")
        rows.append(f"  {'тикер':<12}{'сторона':<6}{'цена':>12}{'qty':>8}{'стоит, мс':>10}")
        for d in report.top_wall[:10]: rows.append(f"  {d['symbol']:<12}{d['side']:<6}{d['price']:>12}{d['qty']:>8}{d['held_ms']:>10}")
    if report.warnings:
        rows.append("")
        for w in report.warnings: rows.append(f"! {w}")
    return "\n".join(rows)