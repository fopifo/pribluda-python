"""
Приблуда на python — монитор спреда тикера относительно индекса (аналог "пружины" LiveScreener).
Отслеживает отклонение цены тикера от индекса MOEX (IMOEXF) в процентах.
Отдельный модуль, не трогает рабочий pair_monitor.py.

Алгоритм (аналог pair_monitor.py):
- Спред = (price_ticker - price_index) / price_index * 100%
- EMA-база (half_life_sec) — обычный уровень спреда
- Гистерезис: сигнал при threshold, сброс при 0.5*threshold
- Скачивает IMOEXF через MOEX ISS (отдельный запрос, не через iss_quotes_sync)

Настройки per-ticker через spring_settings.json (создадим позже).
"""
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional

import requests

BASE_DIR = Path(__file__).resolve().parent.parent.parent
SPRING_SETTINGS_FILE = BASE_DIR / "spring_settings.json"

_log = logging.getLogger("spring_monitor")
if not _log.handlers:
    _logdir = BASE_DIR / "output"
    _logdir.mkdir(exist_ok=True)
    _handler = logging.FileHandler(_logdir / "spring_monitor.log", encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    _log.addHandler(_handler)
    _log.setLevel(logging.INFO)

DISPLAY_HOLD_SEC = 30.0
IMOEX_URL = "https://iss.moex.com/iss/engines/stock/markets/index/securities/IMOEX.json?iss.meta=off"
IMOEX_PERIOD = 5.0  # секунд между запросами


@dataclass
class SpringSignal:
    """Сигнал отклонения спреда от нормы."""
    ticker: str
    kind: str  # "divergence" | "convergence"
    price_ticker: float
    price_index: float
    spread_pct: float
    baseline_pct: float
    deviation_pct: float
    ts: float

    def __str__(self) -> str:
        labels = {"divergence": "ОТКЛОНЕНИЕ", "convergence": "СХОЖДЕНИЕ"}
        label = labels[self.kind]
        return (
            f"[пружина:{self.ticker}:{label}] "
            f"{self.ticker}={self.price_ticker:.2f} IMOEX={self.price_index:.2f} "
            f"спред={self.spread_pct:+.3f}% обычное={self.baseline_pct:+.3f}% "
            f"отклонение={self.deviation_pct:+.3f}%"
        )


class SpringMonitor:
    """
    Монитор спреда тикера относительно индекса IMOEX.
    Один экземпляр — один тикер.
    """
    RESET_FACTOR = 0.5  # доля от threshold, при которой считаем "сошлось"

    def __init__(
        self,
        ticker: str,
        threshold: float = 0.5,
        half_life_sec: float = 600.0,
    ):
        self.ticker = ticker
        self.threshold = threshold
        self.half_life_sec = half_life_sec

        self.last_price_ticker: Optional[float] = None
        self.last_price_index: Optional[float] = None

        self.baseline: Optional[float] = None
        self._baseline_ts: Optional[float] = None

        self.triggered = False

        self.last_event_kind: Optional[str] = None
        self.last_event_ts: Optional[float] = None
        self.last_event_deviation: Optional[float] = None

        _log.info(f"[{ticker}] INIT: threshold={threshold}%, half_life={half_life_sec}s")

    def _current_spread(self) -> Optional[float]:
        if self.last_price_ticker is None or self.last_price_index is None:
            return None
        if self.last_price_index == 0:
            return None
        return (self.last_price_ticker - self.last_price_index) / self.last_price_index * 100.0

    def _update_baseline(self, spread: float, ts: float) -> None:
        if self.baseline is None:
            self.baseline = spread
            self._baseline_ts = ts
            return
        dt = max(ts - self._baseline_ts, 0.0)
        alpha = 1 - 0.5 ** (dt / self.half_life_sec) if self.half_life_sec > 0 else 1.0
        self.baseline = self.baseline + alpha * (spread - self.baseline)
        self._baseline_ts = ts

    def _deviation(self, spread: float) -> float:
        if self.baseline is None:
            return 0.0
        return spread - self.baseline

    def _make_signal(self, kind: str, spread: float, deviation: float, ts: float) -> SpringSignal:
        self.last_event_kind = kind
        self.last_event_ts = ts
        self.last_event_deviation = deviation
        return SpringSignal(
            ticker=self.ticker,
            kind=kind,
            price_ticker=self.last_price_ticker,
            price_index=self.last_price_index,
            spread_pct=spread,
            baseline_pct=self.baseline,
            deviation_pct=deviation,
            ts=ts,
        )

    def on_trade(self, symbol: str, price: float, ts: float) -> Optional[SpringSignal]:
        """
        Кормим сюда каждую сделку по ticker.
        Возвращает SpringSignal при переходе через порог (divergence)
        или при возврате к норме (convergence) — с гистерезисом.
        """
        if symbol != self.ticker:
            return None

        self.last_price_ticker = price

        # Если IMOEXF ещё не загружен, ждём
        if self.last_price_index is None:
            return None

        spread = self._current_spread()
        if spread is None:
            return None

        if not self.triggered:
            self._update_baseline(spread, ts)

        if self.baseline is None:
            return None

        deviation = self._deviation(spread)
        reset_level = self.threshold * self.RESET_FACTOR

        if not self.triggered and abs(deviation) >= self.threshold:
            self.triggered = True
            sig = self._make_signal("divergence", spread, deviation, ts)
            _log.info(f"[{self.ticker}] DIVERGENCE: spread={spread:+.3f}%, "
                     f"baseline={self.baseline:+.3f}%, deviation={deviation:+.3f}%")
            return sig

        if self.triggered and abs(deviation) <= reset_level:
            self.triggered = False
            sig = self._make_signal("convergence", spread, deviation, ts)
            _log.info(f"[{self.ticker}] CONVERGENCE: spread={spread:+.3f}%, "
                     f"baseline={self.baseline:+.3f}%, deviation={deviation:+.3f}%")
            return sig

        return None

    def update_index(self, index_price: float) -> None:
        """Обновляет цену индекса (вызывается извне, например из iss_quotes_sync)."""
        self.last_price_index = index_price

    def snapshot(self, now_ts: Optional[float] = None) -> Dict[str, Any]:
        """Текущее состояние для GUI."""
        spread = self._current_spread()
        deviation = self._deviation(spread) if spread is not None and self.baseline is not None else None

        recent_event_kind = None
        if now_ts is not None and self.last_event_ts is not None:
            if now_ts - self.last_event_ts <= DISPLAY_HOLD_SEC:
                recent_event_kind = self.last_event_kind

        return {
            "ticker": self.ticker,
            "price_ticker": self.last_price_ticker,
            "price_index": self.last_price_index,
            "spread_pct": spread,
            "baseline_pct": self.baseline,
            "deviation_pct": deviation,
            "threshold": self.threshold,
            "triggered": self.triggered,
            "recent_event_kind": recent_event_kind,
        }


def load_spring_settings() -> Dict[str, Dict[str, Any]]:
    """Загружает настройки пружин из JSON."""
    if not SPRING_SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(SPRING_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for ticker, cfg in data.items():
        if not isinstance(cfg, dict):
            continue
        out[str(ticker)] = cfg
    return out


def fetch_imoex_price() -> Optional[float]:
    """
    Скачивает текущее значение IMOEXF с MOEX ISS.
    Возвращает float или None при ошибке.
    """
    try:
        r = requests.get(IMOEX_URL, timeout=10)
        r.raise_for_status()
        data = r.json()
        marketdata = data.get("marketdata", {})
        cols = marketdata.get("columns", [])
        rows = marketdata.get("data", [])
        if not rows:
            return None
        idx = {c: i for i, c in enumerate(cols)}
        last_idx = idx.get("LAST")
        if last_idx is None or last_idx >= len(rows[0]):
            return None
        val = rows[0][last_idx]
        return float(val) if val is not None else None
    except Exception as e:
        _log.warning(f"IMOEX fetch failed: {e}")
        return None