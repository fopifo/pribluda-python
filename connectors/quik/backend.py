"""
Приблуда на python — бэкенд: Alor WebSocket стрим + лента моста CScalp.
v7.1: сделка из очереди пишется в TradeAccumulator ДО кормления детекторов.
v7.2 (2026-11-17): ВТОРОЙ ИСТОЧНИК — лента моста data/pbsdk_trades_DATE.jsonl.
Мост кормит детекторы ТОЛЬКО по тем тикерам, которых нет в потоке Алора
(юань/фьючерсы/валюта). Двойного счёта нет: тикеры, реально приходящие из
Алора, из ленты моста пропускаются. Файлы источников не смешиваются.
"""
import sys
import json
import time
import threading
import logging
import queue
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core.state import SharedState
from core.config import get_detector_configs
from core.config import USE_STREAM_GRID, STREAM_MIN_REPEATS, STREAM_TOL
from core.config import USE_ALOR_STREAM, ALOR_STREAM_TICKERS, ALOR_STREAM_DEPTH
from core.ticker_settings import load_settings
from detectors.interval_robot import IntervalRobotDetector
from modules.stream_grid import StreamGrid
from modules.arbitrage.spring_monitor import SpringMonitor, load_spring_settings, fetch_imoex_price

CSV = BASE / "data" / "quik_trades.csv"
MSK = ZoneInfo("Europe/Moscow")

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

# --- Второй источник: лента моста CScalp ---
USE_BRIDGE_TAPE = True
BRIDGE_GRACE_SEC = 20.0     # ждём, пока Алор отметит свои тикеры
BRIDGE_POLL_SEC = 2.0       # пауза, если файла моста ещё нет


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
        self._feed_times = []

        # Какие тикеры реально кормит Алор (чтобы мост их не дублировал)
        self._alor_syms = set()
        self._src_lock = threading.Lock()
        self._bridge_fed = 0
        self._bridge_skipped = 0

        self.spring_settings = load_spring_settings()
        self.spring_monitors = {}
        for ticker, cfg in self.spring_settings.items():
            threshold = cfg.get("threshold", 0.5)
            half_life = cfg.get("half_life_sec", 600.0)
            self.spring_monitors[ticker] = SpringMonitor(ticker, threshold, half_life)
            _log.info(f"[spring] created monitor for {ticker}: threshold={threshold}%, half_life={half_life}s")

        self.use_stream_grid = USE_STREAM_GRID
        self.stream_grid = StreamGrid(min_repeats=STREAM_MIN_REPEATS, tol=STREAM_TOL) if self.use_stream_grid else None
        self._grid_lock = threading.Lock()
        _log.info(f"[stream_grid] enabled={self.use_stream_grid} mr={STREAM_MIN_REPEATS} tol={STREAM_TOL}")

        self.use_alor_stream = USE_ALOR_STREAM
        self._trade_queue = queue.Queue(maxsize=10000)
        self._alor_client = None
        self._alor_accumulator = None
        if self.use_alor_stream:
            from connectors.alor.stream_client import AlorStreamClient, load_env_value
            from connectors.alor.trade_accumulator import TradeAccumulator
            try:
                refresh_token = load_env_value("ALOR_REFRESH_TOKEN")
                self._alor_accumulator = TradeAccumulator()
                tickers = ALOR_STREAM_TICKERS or list(self.allowed)
                self._alor_client = AlorStreamClient(
                    refresh_token=refresh_token,
                    tickers=tickers,
                    depth=ALOR_STREAM_DEPTH,
                    on_trade=lambda trade: self._trade_queue.put(trade)
                )
                _log.info(f"[alor_stream] enabled, tickers={len(tickers)}, depth={ALOR_STREAM_DEPTH}")
            except Exception as e:
                _log.error(f"[alor_stream] init failed: {e}")
                self.use_alor_stream = False

        _log.info(f"Backend init: {len(self.allowed)} tickers allowed, "
                  f"{len(self.spring_monitors)} spring monitors, "
                  f"alor_stream={self.use_alor_stream}, bridge_tape={USE_BRIDGE_TAPE}")

    def _dets_for(self, sym):
        if sym not in self.dets:
            ov = self.settings.get(sym, {})
            min_q = ov.get("min_qty", 1)
            self.dets[sym] = [IntervalRobotDetector(sym, c)
                              for c in get_detector_configs(sym, min_q, ov)]
            _log.info(f"[{sym}] created {len(self.dets[sym])} detector(s)")
        return self.dets[sym]

    def feed(self, trade, source="alor"):
        _t0 = time.time()
        sym = trade.get("symbol")
        if not sym or sym not in self.allowed:
            return

        if source == "alor":
            with self._src_lock:
                self._alor_syms.add(sym)
        else:
            with self._src_lock:
                if sym in self._alor_syms:
                    self._bridge_skipped += 1
                    return

        _lag_ms = time.time() * 1000.0 - float(trade.get("timestamp") or 0)
        if _lag_ms > 2000:
            _log.warning(f"[LAG] {sym} src={source} lag_ms={_lag_ms:.0f}")

        _t1 = time.time()
        for d in self._dets_for(sym):
            d.on_trade(trade)
            for ts_sec in d.drain_confirms():
                self.confirm_times[sym] = ts_sec
        _dt_robot = (time.time() - _t1) * 1000.0
        if _dt_robot > 10.0:
            _log.warning(f"[SLOW_ROBOT] {sym} robot_ms={_dt_robot:.1f}")

        _t2 = time.time()
        if self.use_stream_grid:
            sig = self.stream_grid.on_trade(sym, trade["side"], trade["qty"], trade["timestamp"])
            if sig and sig["qty_max"] >= STREAM_MIN_QTY:
                with self._grid_lock:
                    self.shared.grid_signals.append(sig)
                _log.info(f"[stream_grid] SIGNAL {sig['ticker']} {sig['side']} "
                          f"qty=[{sig['qty_min']},{sig['qty_max']}] int={sig['period']:.1f}s src={source}")
        _dt_grid = (time.time() - _t2) * 1000.0
        if _dt_grid > 10.0:
            _log.warning(f"[SLOW_GRID] {sym} grid_ms={_dt_grid:.1f}")

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

    def parse(self, line):
        line = line.strip()
        if not line or line.startswith("#"):
            return None
        p = line.split(";")
        if len(p) < 5:
            return None
        try:
            return {"symbol": p[0], "qty": int(float(p[1])), "price": float(p[2]),
                    "side": p[3], "timestamp": int(p[4])}
        except ValueError:
            return None

    def _update_batch_flash(self, now):
        recent = [s for s, ts in self.confirm_times.items() if now - ts <= BATCH_WINDOW_SEC]
        if len(recent) >= BATCH_MIN_TICKERS:
            self.shared.batch_flash = {s: now for s in recent}
        else:
            self.shared.batch_flash = {}
        self.confirm_times = {s: t for s, t in self.confirm_times.items() if now - t <= 60}

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
            rows = []
            for dets in self.dets.values():
                for d in dets:
                    d.check_overdue(now)
                    rows.extend(d.get_active_snapshot(now))
            self.shared.rows = rows
            if self.use_stream_grid:
                now_ms = now * 1000
                with self._grid_lock:
                    fresh = [s for s in self.shared.grid_signals if now_ms - s["end_ms"] <= 60000]
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

    def _alor_stream_loop(self):
        if not self.use_alor_stream or self._alor_client is None:
            return
        _log.info("[alor_stream] starting WebSocket stream...")
        self._alor_client.run_sync()

    def bridge_tape_loop(self):
        """Второй источник: лента моста CScalp (pbsdk_trades_DATE.jsonl).
        Кормит только тикеры, которых нет в потоке Алора."""
        if not USE_BRIDGE_TAPE:
            return
        time.sleep(BRIDGE_GRACE_SEC)
        with self._src_lock:
            alor_n = len(self._alor_syms)
        _log.info(f"[bridge_tape] start (алор кормит {alor_n} тикеров; мост — остальные)")

        cur_date = None
        fh = None
        day0_ms = 0
        while True:
            try:
                today = datetime.now(MSK).strftime("%Y-%m-%d")
                if today != cur_date:
                    if fh is not None:
                        fh.close()
                        fh = None
                    cur_date = today
                    day0_ms = int(datetime.now(MSK).replace(
                        hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
                    _log.info(f"[bridge_tape] день {today}")

                path = BASE / "data" / f"pbsdk_trades_{today}.jsonl"
                if fh is None:
                    if not path.exists():
                        time.sleep(BRIDGE_POLL_SEC)
                        continue
                    fh = open(path, "r", encoding="utf-8", errors="ignore")
                    _log.info(f"[bridge_tape] читаю {path.name}")

                line = fh.readline()
                if not line:
                    time.sleep(0.2)
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sym = d.get("symbol")
                ts = d.get("timestamp")
                side = (d.get("side") or "").lower()
                if not sym or ts is None or side not in ("buy", "sell"):
                    continue
                if sym not in self.allowed:
                    continue
                try:
                    qty = int(float(d.get("qty") or 0))
                    price = float(d.get("price") or 0)
                except (TypeError, ValueError):
                    continue
                if qty <= 0 or int(ts) < day0_ms:
                    continue
                self.feed({"symbol": sym, "qty": qty, "price": price,
                           "side": side, "timestamp": int(ts)}, source="bridge")
                self._bridge_fed += 1
                if self._bridge_fed % 5000 == 0:
                    _log.info(f"[bridge_tape] fed={self._bridge_fed} "
                              f"skipped_alor_dup={self._bridge_skipped}")
            except Exception as e:
                _log.error(f"[bridge_tape] error: {type(e).__name__}: {e}")
                if fh is not None:
                    try:
                        fh.close()
                    except Exception:
                        pass
                    fh = None
                time.sleep(BRIDGE_POLL_SEC)

    def run(self):
        day0_ms = int(datetime.now(MSK).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)

        if self.use_alor_stream:
            _log.info(f"[alor_stream] backfill from {self._alor_accumulator.path}")
            backfill_trades = self._alor_accumulator.load_backfill()
            for trade in backfill_trades:
                if trade["timestamp"] >= day0_ms:
                    self.feed(trade, source="alor")
            _log.info(f"[alor_stream] backfill done: {len(backfill_trades)} trades, "
                      f"{self.trades_fed} fed (after day0 filter)")
            _log.info("[alor_stream] switching to live queue mode")
            self._reader_t0 = time.time()
            self._reader_count = 0
            while True:
                try:
                    trade = self._trade_queue.get(timeout=1.0)
                    self._alor_accumulator.on_trade(trade)
                    if trade["timestamp"] >= day0_ms:
                        self.feed(trade, source="alor")
                    self._reader_count += 1
                    if self._reader_count % 1000 == 0:
                        elapsed = time.time() - self._reader_t0
                        rate = self._reader_count / max(elapsed, 0.001)
                        backlog = self._trade_queue.qsize()
                        _log.info(f"[READER] count={self._reader_count} "
                                  f"rate={rate:.0f}/s backlog_queue={backlog}")
                except queue.Empty:
                    continue
        else:
            _log.info(f"Starting CSV reader, day0_ms={day0_ms}")
            try:
                with open(CSV, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(0, 2)
                    file_size = f.tell()
                    read_size = min(5000000, file_size)
                    f.seek(file_size - read_size)
                    f.readline()
                    for line in f:
                        t = self.parse(line)
                        if t and t["timestamp"] >= day0_ms:
                            self.feed(t, source="alor")
                        self.lines_read += 1
                        if self.lines_read % 1000 == 0:
                            _log.info(f"Backfill progress: {self.lines_read} lines, "
                                      f"{self.trades_fed} trades")
                    _log.info(f"Backfill done: {self.lines_read} lines, "
                              f"{self.trades_fed} trades fed")
            except FileNotFoundError:
                _log.warning(f"CSV not found: {CSV}")

            _log.info("Switching to live tail mode (CSV ms, no overwrite)")
            self._reader_t0 = time.time()
            self._reader_count = 0
            while True:
                try:
                    with open(CSV, "r", encoding="utf-8", errors="ignore") as f:
                        f.seek(0, 2)
                        while True:
                            line = f.readline()
                            if not line:
                                time.sleep(0.05)
                                continue
                            t = self.parse(line)
                            if t and t["timestamp"] >= day0_ms:
                                self.feed(t, source="alor")
                            self._reader_count += 1
                            if self._reader_count % 1000 == 0:
                                elapsed = time.time() - self._reader_t0
                                rate = self._reader_count / max(elapsed, 0.001)
                                pos = f.tell()
                                f.seek(0, 2)
                                end = f.tell()
                                f.seek(pos)
                                backlog = end - pos
                                _log.info(f"[READER] count={self._reader_count} "
                                          f"rate={rate:.0f}/s backlog_bytes={backlog}")
                except FileNotFoundError:
                    time.sleep(1)


def start_backend(shared: SharedState):
    """Точка входа для запуска бэкенда в отдельном потоке."""
    b = QuikBackend(shared)
    threading.Thread(target=b.run, daemon=True, name="QuikReader").start()
    threading.Thread(target=b.publisher, daemon=True, name="QuikPublisher").start()
    threading.Thread(target=b.imoex_updater, daemon=True, name="IMOEXUpdater").start()
    if b.use_alor_stream:
        threading.Thread(target=b._alor_stream_loop, daemon=True, name="AlorStream").start()
    if USE_BRIDGE_TAPE:
        threading.Thread(target=b.bridge_tape_loop, daemon=True, name="BridgeTape").start()
    _log.info(f"Backend threads started (alor_stream={b.use_alor_stream}, bridge_tape={USE_BRIDGE_TAPE}).")