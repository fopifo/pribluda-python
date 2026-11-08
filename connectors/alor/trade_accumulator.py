"""
Локальный накопитель сделок из стрима Алора (замена CSV backfill QUIK).

Проблема: Алор не даёт историю за ТЕКУЩИЙ день через API (только за
предыдущие), depth=N в подписке в ДСВД возвращает пустоту. При рестарте
приблуды внутри дня терялись все сделки с начала дня.

Решение: файл-накопитель data/live_trades_YYYY-MM-DD.jsonl (на календарный
день МСК):
- каждая сделка стрима дописывается в файл в реальном времени;
- при рестарте load_backfill() читает файл и отдаёт сделки в порядке
  timestamp (кормить детекторы);
- в конце дня tools/end_of_day.py конвертирует файл в
  data/{SYMBOL}_{DATE}.json (формат Алора для alor_download_day / v11).

Дедупликация: high-water mark по id сделки на тикер (id растут внутри
потока тикера; снимок depth при реконнекте даёт id <= марки — отбрасываются).
Тяжёлого множества всех id дня нет.

Потокобезопасность: on_trade вызывается из потока стрима; запись под
RLock, flush пачками (FLUSH_TRADES сделок или FLUSH_INTERVAL_SEC).
"""
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")

FLUSH_INTERVAL_SEC = 0.2
FLUSH_TRADES = 200


def trade_date_msk(ts_ms: int) -> str:
    """Календарный день МСК для timestamp сделки."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=MSK).strftime("%Y-%m-%d")


class TradeAccumulator:
    def __init__(self, data_dir=None, date=None):
        if data_dir is None:
            data_dir = Path(__file__).resolve().parent.parent.parent / "data"
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        self.date = date or datetime.now(MSK).strftime("%Y-%m-%d")
        self.path = self.data_dir / f"live_trades_{self.date}.jsonl"
        self._hwm = {}
        self._lock = threading.RLock()
        self._fh = None
        self._pending = 0
        self._last_flush = time.time()
        self.written = 0
        self.deduped = 0
        self._load_hwm()
        self._open()

    def _open(self):
        self._fh = open(self.path, "a", encoding="utf-8")

    def _load_hwm(self):
        """Восстанавливает high-water marks из файла дня при старте."""
        if not self.path.exists():
            return
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    t = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sym = t.get("symbol")
                tid = t.get("id")
                if sym and tid is not None:
                    if tid > self._hwm.get(sym, -1):
                        self._hwm[sym] = tid

    def _rotate(self, new_date):
        """Смена календарного дня на ходу: закрыть старый файл, открыть новый."""
        self.close()
        self.date = new_date
        self.path = self.data_dir / f"live_trades_{new_date}.jsonl"
        self._hwm = {}
        self._load_hwm()
        self._open()

    def on_trade(self, trade):
        """Sync-callback для AlorStreamClient(on_trade=...)."""
        tid = trade.get("id")
        sym = trade.get("symbol")
        ts = trade.get("timestamp")
        if not sym or tid is None or ts is None:
            return
        with self._lock:
            d = trade_date_msk(ts)
            if d != self.date:
                self._rotate(d)
            if tid <= self._hwm.get(sym, -1):
                self.deduped += 1
                return
            self._hwm[sym] = tid
            rec = dict(trade)
            rec["received_ms"] = int(time.time() * 1000)
            self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self.written += 1
            self._pending += 1
            now = time.time()
            if self._pending >= FLUSH_TRADES or now - self._last_flush >= FLUSH_INTERVAL_SEC:
                self._fh.flush()
                self._pending = 0
                self._last_flush = now

    def load_backfill(self):
        """Все сделки дня из файла, отсортированные по timestamp (для детекторов)."""
        out = []
        if not self.path.exists():
            return out
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        out.sort(key=lambda t: t.get("timestamp", 0))
        return out

    def stats(self):
        with self._lock:
            return {"date": self.date, "path": str(self.path),
                    "written": self.written, "deduped": self.deduped,
                    "tickers": len(self._hwm)}

    def close(self):
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.flush()
                    self._fh.close()
                finally:
                    self._fh = None