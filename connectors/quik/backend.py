"""
Приблуда на python — бэкенд.
v8 (2026-11-17): основной источник ленты — мост CScalp (data/pbsdk_trades_*.jsonl).
Алор — только для вечерних прогонов (replay_alor.py) и сверки.
QUIK-CSV ветка удалена (QUIK не используется с 2026-11-08).
Архитектура: мост пишет JSONL → backend читает в режиме tail → кормит детекторы.
v9 (2026-11-19): добавлен онлайн-агрегатор роботов с confidence scoring.
"""
import sys
import time
import threading
import logging
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core.state import SharedState
from core.config import get_detector_configs
from core.config import USE_STREAM_GRID, STREAM_MIN_REPEATS, STREAM_TOL
from core.ticker_settings import load_settings
from core.robot_aggregator import RobotAggregator
from detectors.interval_robot import IntervalRobotDetector
from modules.stream_grid import StreamGrid
from modules.arbitrage.spring_monitor import SpringMonitor, load_spring_settings, fetch_imoex_price

MSK = ZoneInfo("Europe/Moscow")
DATA_DIR = BASE / "data"

_log = logging.getLogger("quik_backend")
if not _log.handlers:
    _logdir = BASE / "output"
    _logdir.mkdir(exist_ok=True)
    _handler = logging.FileHandler(_logdir / "quik_noconsole.log", encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    _log.addHandler(_handler)
    _log.setLevel(logging.INFO)

BATCH_MIN_TICKERS = 4
BATCH_WINDOW_SEC = 15.0
IMOEX_PERIOD = 5.0
STREAM_MIN_QTY = 2

# Мост: файл ленты и период опроса
BRIDGE_TAPE_POLL_SEC = 1.0
BRIDGE_TAPE_WARN_SEC = 60  # если файл не растёт >60с — предупреждение


class BridgeTapeReader:
    """Читает pbsdk_trades_*.jsonl в режиме tail, кормит детекторы."""

    def __init__(self, allowed: set, on_trade):
        self.allowed = allowed
        self.on_trade = on_trade
        self._fh = None
        self._cur_date = None
        self._last_size = 0
        self._last_write = 0.0
        self._fed = 0
        self._skipped = 0

    def _open_today(self):
        today = datetime.now(MSK).strftime("%Y-%m-%d")
        if today == self._cur_date:
            return
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                pass
        self._cur_date = today
        path = DATA_DIR / f"pbsdk_trades_{today}.jsonl"
        if path.exists():
            self._fh = open(path, "r", encoding="utf-8", errors="ignore")
            self._fh.seek(0, 2)
            self._last_size = self._fh.tell()
            self._last_write = time.time()
            _log.info(f"[bridge_tape] открыт {path.name} (size={self._last_size})")
        else:
            self._fh = None
            self._last_size = 0
            _log.info(f"[bridge_tape] файл {path.name} ещё не создан, жду...")

    def tick(self):
        """Один цикл чтения. Вызывается из потока раз в BRIDGE_TAPE_POLL_SEC."""
        self._open_today()
        if self._fh is None:
            return
        try:
            while True:
                line = self._fh.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sym = d.get("symbol")
                if not sym or sym not in self.allowed:
                    self._skipped += 1
                    continue
                side = (d.get("side") or "").lower()
                if side not in ("buy", "sell"):
                    continue
                try:
                    qty = int(float(d.get("qty") or 0))
                    price = float(d.get("price") or 0)
                    ts = int(d.get("timestamp") or 0)
                except (TypeError, ValueError):
                    continue
                if qty <= 0 or price <= 0 or ts <= 0:
                    continue
                trade = {
                    "symbol": sym,
                    "qty": qty,
                    "price": price,
                    "side": side,
                    "timestamp": ts,
                }
                self.on_trade(trade)
                self._fed += 1
            # проверка что файл растёт
            cur_size = self._fh.tell()
            if cur_size > self._last_size:
                self._last_write = time.time()
                self._last_size = cur_size
            elif time.time() - self._last_write > BRIDGE_TAPE_WARN_SEC:
                _log.warning(f"[bridge_tape] файл не растёт {BRIDGE_TAPE_WARN_SEC}с — мост молчит?")
        except Exception as e:
            _log.error(f"[bridge_tape] error: {e}")
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None

    @property
    def stats(self):
        return {"fed": self._fed, "skipped": self._skipped}


class QuikBackend:
    def __init__(self, shared: SharedState):
        self.shared = shared
        self.settings = load_settings()
        self.allowed = set(self.settings.keys())
        self.dets = {}
        self.lines_read = 0
        self.trades_fed = 0
        self.confirm_times = {}
        self._reader_t0 = None
        self._reader_count = 0

        # Онлайн-агрегатор роботов с confidence scoring
        self.aggregator = RobotAggregator()
        _log.info("[aggregator] online robot aggregator initialized")

        # SpringMonitor
        self.spring_settings = load_spring_settings()
        self.spring_monitors = {}
        for ticker, cfg in self.spring_settings.items():
            threshold = cfg.get("threshold", 0.5)
            half_life = cfg.get("half_life_sec", 600.0)
            self.spring_monitors[ticker] = SpringMonitor(ticker, threshold, half_life)
            _log.info(f"[spring] created monitor for {ticker}: threshold={threshold}%, half_life={half_life}s")

        # StreamGrid
        self.use_stream_grid = USE_STREAM_GRID
        self.stream_grid = StreamGrid(min_repeats=STREAM_MIN_REPEATS, tol=STREAM_TOL) if self.use_stream_grid else None
        self._grid_lock = threading.Lock()
        _log.info(f"[stream_grid] enabled={self.use_stream_grid} mr={STREAM_MIN_REPEATS} tol={STREAM_TOL}")

        # Мост: читалка pbsdk_trades_*.jsonl
        self.bridge_reader = BridgeTapeReader(self.allowed, self.feed)

        _log.info(f"Backend init: {len(self.allowed)} tickers allowed, "
                  f"{len(self.spring_monitors)} spring monitors, "
                  f"source=bridge_tape (pbsdk_trades)")

    def _dets_for(self, sym):
        if sym not in self.dets:
            ov = self.settings.get(sym, {})
            min_q = ov.get("min_qty", 1)
            self.dets[sym] = [IntervalRobotDetector(sym, c)
                              for c in get_detector_configs(sym, min_q, ov)]
            _log.info(f"[{sym}] created {len(self.dets[sym])} detector(s)")
        return self.dets[sym]

    def feed(self, trade):
        _t0 = time.time()
        _lag_ms = time.time() * 1000.0 - float(trade.get("timestamp") or 0)
        if _lag_ms > 2000:
            _log.warning(f"[LAG] {trade.get('symbol')} lag_ms={_lag_ms:.0f}")
        sym = trade["symbol"]
        if sym not in self.allowed:
            return

        # Interval robot detector
        _t1 = time.time()
        for d in self._dets_for(sym):
            d.on_trade(trade)
            for ts_sec in d.drain_confirms():
                self.confirm_times[sym] = ts_sec
        _dt_robot = (time.time() - _t1) * 1000.0
        if _dt_robot > 10.0:
            _log.warning(f"[SLOW_ROBOT] {sym} robot_ms={_dt_robot:.1f}")

        # StreamGrid
        _t2 = time.time()
        if self.use_stream_grid:
            sig = self.stream_grid.on_trade(
                sym, trade["side"], trade["qty"], trade["timestamp"]
            )
            if sig and sig["qty_max"] >= STREAM_MIN_QTY:
                with self._grid_lock:
                    self.shared.grid_signals.append(sig)
                _log.info(
                    f"[stream_grid] SIGNAL {sig['ticker']} {sig['side']} "
                    f"qty=[{sig['qty_min']},{sig['qty_max']}] "
                    f"int={sig['period']:.1f}s"
                )
        _dt_grid = (time.time() - _t2) * 1000.0
        if _dt_grid > 10.0:
            _log.warning(f"[SLOW_GRID] {sym} grid_ms={_dt_grid:.1f}")

        # Spring monitor
        _t3 = time.time()
        if sym in self.spring_monitors:
            price = trade.get("price")
            ts_sec = trade["timestamp"] / 1000.0
            if price is not None:
                self.spring_monitors[sym].on_trade(sym, price, ts_sec)
        _dt_spring = (time.time() - _t3) * 1000.0
        if _dt_spring > 10.0:
            _log.warning(f"[SLOW_SPRING] {sym} spring_ms={_dt_spring:.1f}")

        self.trades_fed += 1
        _dt = (time.time() - _t0) * 1000.0
        if _dt > 50.0:
            _log.warning(f"[SLOW_FEED] {sym} feed_ms={_dt:.1f}")

    def _update_batch_flash(self, now):
        recent = [s for s, ts in self.confirm_times.items() if now - ts <= BATCH_WINDOW_SEC]
        if len(recent) >= BATCH_MIN_TICKERS:
            self.shared.batch_flash = {s: now for s in recent}
        else:
            self.shared.batch_flash = {}
        self.confirm_times = {s: t for s, t in self.confirm_times.items()
                              if now - t <= 60}

    def _update_spring_rows(self, now):
        rows = []
        for ticker, monitor in self.spring_monitors.items():
            snap = monitor.snapshot(now_ts=now)
            if snap["price_ticker"] is not None and snap["price_index"] is not None:
                rows.append(snap)
        self.shared.spring_rows = rows

    def publisher(self):
        while True:
            now = datetime.now().timestamp()
            self._update_batch_flash(now)
            
            # Собираем snapshot от всех детекторов
            detector_snapshots = []
            for dets in self.dets.values():
                for d in dets:
                    d.check_overdue(now)
                    rows = d.get_active_snapshot(now)
                    detector_snapshots.extend(rows)
            
            # Передаём snapshot'ы в агрегатор для обновления confidence
            for snap in detector_snapshots:
                self.aggregator.update_from_snapshot(snap, now)
            
            # Получаем отсортированный список роботов с confidence
            self.shared.rows = self.aggregator.get_all(min_confidence=0.5)
            
            if self.use_stream_grid:
                now_ms = now * 1000
                with self._grid_lock:
                    fresh = [s for s in self.shared.grid_signals
                             if now_ms - s["end_ms"] <= 60000]
                    self.shared.grid_signals = fresh
            self._update_spring_rows(now)
            time.sleep(1.0)

    def imoex_updater(self):
        _log.info(f"IMOEX updater started (period={IMOEX_PERIOD}s)")
        while True:
            price = fetch_imoex_price()
            if price is not None:
                for monitor in self.spring_monitors.values():
                    monitor.update_index(price)
                _log.debug(f"IMOEX updated: {price}")
            else:
                _log.warning("IMOEX fetch failed")
            time.sleep(IMOEX_PERIOD)

    def bridge_tape_loop(self):
        """Основной поток чтения: мост CScalp (pbsdk_trades_*.jsonl)."""
        _log.info("[bridge_tape] start")
        self._reader_t0 = time.time()
        self._reader_count = 0
        while True:
            self.bridge_reader.tick()
            self._reader_count += 1
            if self._reader_count % 1000 == 0:
                elapsed = time.time() - self._reader_t0
                rate = self._reader_count / max(elapsed, 0.001)
                stats = self.bridge_reader.stats
                _log.info(f"[READER] count={self._reader_count} "
                          f"rate={rate:.0f}/s fed={stats['fed']} skipped={stats['skipped']}")
            time.sleep(BRIDGE_TAPE_POLL_SEC)

    def run(self):
        """Точка входа: запускаем bridge_tape_loop."""
        self.bridge_tape_loop()


def start_backend(shared: SharedState):
    """Точка входа для запуска бэкенда в отдельном потоке."""
    b = QuikBackend(shared)
    threading.Thread(target=b.run, daemon=True, name="BridgeTapeReader").start()
    threading.Thread(target=b.publisher, daemon=True, name="QuikPublisher").start()
    threading.Thread(target=b.imoex_updater, daemon=True, name="IMOEXUpdater").start()
    _log.info(f"Backend threads started (source=bridge_tape).")