"""
Приблуда на python - бэкенд для Quik (чтение CSV ленты).
Оптимизировано: быстрый старт (последние 500KB) + миллисекундная точность.
v2: live-ветка НЕ перезаписывает timestamp (настоящие мс из CSV).
v3: batch_flash - мигание "пачки" роботов: >=4 тикеров подтвердились за 15с.
v4: интеграция SpringMonitor (спред тикера относительно IMOEXF).
"""
import sys, time, threading, logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core.state import SharedState
from core.config import get_detector_configs
from core.ticker_settings import load_settings
from detectors.interval_robot import IntervalRobotDetector
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

# Волна: столько тикеров должны подтвердиться за окно, чтобы мигать
BATCH_MIN_TICKERS = 4
BATCH_WINDOW_SEC = 15.0

# IMOEXF: период обновления индекса
IMOEX_PERIOD = 5.0


class QuikBackend:
    def __init__(self, shared: SharedState):
        self.shared = shared
        self.settings = load_settings()
        self.allowed = set(self.settings.keys())
        self.dets = {}
        self.lines_read = 0
        self.trades_fed = 0
        self.confirm_times = {}   # symbol -> ts последнего подтверждения
        
        # NEW: SpringMonitor для спредов тикеров относительно IMOEXF
        self.spring_settings = load_spring_settings()
        self.spring_monitors = {}
        for ticker, cfg in self.spring_settings.items():
            threshold = cfg.get("threshold", 0.5)
            half_life = cfg.get("half_life_sec", 600.0)
            self.spring_monitors[ticker] = SpringMonitor(ticker, threshold, half_life)
            _log.info(f"[spring] created monitor for {ticker}: threshold={threshold}%, half_life={half_life}s")
        
        _log.info(f"Backend init: {len(self.allowed)} tickers allowed, {len(self.spring_monitors)} spring monitors")

    def _dets_for(self, sym):
        if sym not in self.dets:
            ov = self.settings.get(sym, {})
            min_q = ov.get("min_qty", 1)
            self.dets[sym] = [IntervalRobotDetector(sym, c)
                              for c in get_detector_configs(sym, min_q, ov)]
            _log.info(f"[{sym}] created {len(self.dets[sym])} detector(s)")
        return self.dets[sym]

    def feed(self, trade):
        sym = trade["symbol"]
        if sym not in self.allowed:
            return
        
        # Interval robot detector (рабочий код, не трогать)
        for d in self._dets_for(sym):
            d.on_trade(trade)
            for ts_sec in d.drain_confirms():
                self.confirm_times[sym] = ts_sec
        
        # NEW: Spring monitor (спред тикера относительно IMOEXF)
        if sym in self.spring_monitors:
            price = trade.get("price")
            ts_sec = trade["timestamp"] / 1000.0
            if price is not None:
                self.spring_monitors[sym].on_trade(sym, price, ts_sec)
        
        self.trades_fed += 1

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
        """Наполняет shared.batch_flash: мигают тикеры волны (>=4 за 15с)."""
        recent = [s for s, ts in self.confirm_times.items() if now - ts <= BATCH_WINDOW_SEC]
        if len(recent) >= BATCH_MIN_TICKERS:
            self.shared.batch_flash = {s: now for s in recent}
        else:
            self.shared.batch_flash = {}
        # чистим старые
        self.confirm_times = {s: t for s, t in self.confirm_times.items()
                              if now - t <= 60}

    def _update_spring_rows(self, now):
        """NEW: Наполняет shared.spring_rows - текущее состояние спредов."""
        rows = []
        for ticker, monitor in self.spring_monitors.items():
            snap = monitor.snapshot(now_ts=now)
            if snap["price_ticker"] is not None and snap["price_index"] is not None:
                rows.append(snap)
        self.shared.spring_rows = rows

    def publisher(self):
        """Поток обновления GUI (раз в секунду собирает снапшоты)."""
        while True:
            now = datetime.now().timestamp()
            self._update_batch_flash(now)
            
            # Interval robot detector rows
            rows = []
            for dets in self.dets.values():
                for d in dets:
                    d.check_overdue(now)
                    rows.extend(d.get_active_snapshot(now))
            self.shared.rows = rows
            
            # NEW: Spring monitor rows
            self._update_spring_rows(now)
            
            time.sleep(1.0)

    def imoex_updater(self):
        """NEW: Поток обновления IMOEXF (раз в IMOEX_PERIOD секунд)."""
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

    def run(self):
        """Основной поток чтения CSV."""
        day0_ms = int(datetime.now(MSK).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        _log.info(f"Starting CSV reader, day0_ms={day0_ms}")

        # 1. Быстрый backfill: последние ~500KB, метки времени из CSV (мс точность)
        try:
            with open(CSV, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(0, 2)
                file_size = f.tell()
                read_size = min(500000, file_size)
                f.seek(file_size - read_size)
                f.readline()  # пропускаем неполную строку
                for line in f:
                    t = self.parse(line)
                    if t and t["timestamp"] >= day0_ms:
                        self.feed(t)
                    self.lines_read += 1
                    if self.lines_read % 1000 == 0:
                        _log.info(f"Backfill progress: {self.lines_read} lines, {self.trades_fed} trades")
            _log.info(f"Backfill done: {self.lines_read} lines, {self.trades_fed} trades fed")
        except FileNotFoundError:
            _log.warning(f"CSV not found: {CSV}")

        # 2. Live tail: метки времени из CSV (мс) - БЕЗ перезаписи arrival-time.
        _log.info("Switching to live tail mode (CSV ms, no overwrite)")
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
                            self.feed(t)
            except FileNotFoundError:
                time.sleep(1)

def start_backend(shared: SharedState):
    """Точка входа для запуска бэкенда в отдельном потоке."""
    b = QuikBackend(shared)
    threading.Thread(target=b.run, daemon=True, name="QuikReader").start()
    threading.Thread(target=b.publisher, daemon=True, name="QuikPublisher").start()
    threading.Thread(target=b.imoex_updater, daemon=True, name="IMOEXUpdater").start()  # NEW
    _log.info("Quik backend threads started (reader, publisher, imoex_updater).")
