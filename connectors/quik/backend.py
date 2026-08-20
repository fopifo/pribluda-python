"""
Приблуда на python — бэкенд для Quik (чтение CSV ленты).
Оптимизировано: быстрый старт (последние 500KB) + миллисекундная точность для живых сделок.
Фикс: в live-ветке больше не перезаписываем timestamp arrival-time — lua v3.10
пишет настоящие миллисекунды из alltrade.datetime.msec. Перезапись давала
джиттер ±50-200мс от опроса и рвала ритм роботов.
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


class QuikBackend:
    def __init__(self, shared: SharedState):
        self.shared = shared
        self.settings = load_settings()
        self.allowed = set(self.settings.keys())
        self.dets = {}
        self.lines_read = 0
        self.trades_fed = 0
        _log.info(f"Backend init: {len(self.allowed)} tickers allowed")

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
        for d in self._dets_for(sym):
            d.on_trade(trade)
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

    def publisher(self):
        """Поток обновления GUI (раз в секунду собирает снапшоты)."""
        while True:
            now = datetime.now().timestamp()
            rows = []
            for dets in self.dets.values():
                for d in dets:
                    d.check_overdue(now)
                    rows.extend(d.get_active_snapshot(now))
            self.shared.rows = rows
            time.sleep(1.0)

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

        # 2. Live tail: метки времени из CSV (мс) — БЕЗ перезаписи arrival-time.
        # Lua v3.10 пишет настоящие мс из alltrade.datetime.msec; фолбэк на
        # arrival-time больше не нужен — он только добавлял джиттер ±50-200мс
        # и рвал ритм роботов.
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
    _log.info("Quik backend threads started.")