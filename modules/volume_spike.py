"""
Приблуда на python — детектор всплесков объёма (аналог VOLUME из LiveScreener).
Ищет всплески объёма по стороне (buy/sell) за последние 1/2/3/4 секунды.
Отдельный модуль, не заменяет interval_robot — работает параллельно.

Алгоритм:
- Скользящее окно объёмов по сторонам (buy/sell) для каждого тикера
- Проверка 4 окон: 1s, 2s, 3s, 4s
- Сигнал, если объём >= min_volume И >= threshold_multiplier × средний за 5 минут
- Кулдаун 10 секунд per ticker per side (анти-шум)
- Настройки per-ticker через ticker_settings.json
"""
import logging
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any
from detectors.base import Signal

_log = logging.getLogger("volume_spike")
if not _log.handlers:
    _logdir = Path(__file__).resolve().parent.parent / "output"
    _logdir.mkdir(exist_ok=True)
    _handler = logging.FileHandler(_logdir / "volume_spike.log", encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    _log.addHandler(_handler)
    _log.setLevel(logging.INFO)


class VolumeSpikeDetector:
    """
    Детектор всплесков объёма за короткие окна (1-4 секунды).
    Один экземпляр — один тикер.
    """
    name = "volume-spike"
    
    def __init__(self, symbol: str, settings: Dict[str, Any]):
        self.symbol = symbol
        self.settings = settings
        
        # Настройки (defaults + overrides из ticker_settings.json)
        self.enabled = settings.get("volume_spike_enabled", True)
        self.windows = settings.get("volume_spike_windows", [1, 2, 3, 4])
        self.min_volume = settings.get("volume_spike_min_volume", 500)
        self.threshold_multiplier = settings.get("volume_spike_threshold_multiplier", 2.0)
        self.cooldown_sec = settings.get("volume_spike_cooldown_sec", 10)
        
        # Per-ticker override
        if "volume_spike_min_volume_override" in settings:
            self.min_volume = settings["volume_spike_min_volume_override"]
        
        # Скользящие окна: {(side, window_sec): deque[(timestamp_sec, qty)]}
        self.trades_by_window = defaultdict(lambda: deque())
        
        # История для расчёта среднего (последние 5 минут): {side: deque[(timestamp_sec, qty)]}
        self.history = defaultdict(lambda: deque())
        
        # Кулдауны: {(side, window_sec): timestamp_sec}
        self.last_signal = {}
        
        _log.info(f"[{symbol}] INIT: enabled={self.enabled}, windows={self.windows}, "
                  f"min_volume={self.min_volume}, threshold={self.threshold_multiplier}x")
    
    def _prune_old(self, dq: deque, cutoff_sec: float):
        """Удаляет сделки старше cutoff_sec из deque."""
        while dq and dq[0][0] < cutoff_sec:
            dq.popleft()
    
    def _calc_avg_volume(self, side: str, now_sec: float, window_sec: float = 300) -> float:
        """
        Средний объём за последние window_sec (по умолчанию 5 минут).
        Возвращает объём в секунду.
        Если история короче 60 секунд, возвращает 0 (не применяем threshold).
        """
        dq = self.history[side]
        self._prune_old(dq, now_sec - window_sec)
        
        if not dq:
            return 0.0
        
        total_qty = sum(q for _, q in dq)
        duration = now_sec - dq[0][0] if len(dq) > 1 else window_sec
        
        # Если история короче 60 секунд, не применяем threshold
        if duration < 60.0:
            return 0.0
        
        return total_qty / max(duration, 1.0)
    
    def _check_window(self, side: str, window_sec: int, now_sec: float) -> tuple[bool, int, float]:
        """
        Проверяет окно window_sec на всплеск.
        Возвращает (found, total_qty, avg_volume).
        """
        dq = self.trades_by_window[(side, window_sec)]
        self._prune_old(dq, now_sec - window_sec)
        
        if not dq:
            return False, 0, 0.0
        
        total_qty = sum(q for _, q in dq)
        avg_volume = self._calc_avg_volume(side, now_sec, window_sec=300)
        
        # Проверка порога
        if total_qty < self.min_volume:
            return False, 0, avg_volume
        
        # Применяем threshold_multiplier только если есть достаточная история
        if avg_volume > 0 and total_qty < avg_volume * window_sec * self.threshold_multiplier:
            return False, 0, avg_volume
        
        # Проверка кулдауна
        key = (side, window_sec)
        if key in self.last_signal:
            if now_sec - self.last_signal[key] < self.cooldown_sec:
                return False, 0, avg_volume
        
        return True, total_qty, avg_volume
    
    def on_trade(self, trade: Dict[str, Any]) -> List[Signal]:
        """
        Обрабатывает сделку, проверяет всплески во всех окнах.
        Возвращает список сигналов (обычно 0 или 1).
        """
        if not self.enabled:
            return []
        
        qty = trade["qty"]
        side = trade["side"]
        ts_ms = trade["timestamp"]
        ts_sec = ts_ms / 1000.0
        
        # Добавляем сделку во все окна
        for window in self.windows:
            self.trades_by_window[(side, window)].append((ts_sec, qty))
        
        # Добавляем в историю для среднего
        self.history[side].append((ts_sec, qty))
        
        # Проверяем все окна на всплеск
        signals = []
        for window in self.windows:
            found, total_qty, avg_volume = self._check_window(side, window, ts_sec)
            if found:
                # Считаем количество сделок в окне
                dq = self.trades_by_window[(side, window)]
                repeats = len(dq)
                
                # Находим начало окна
                start_ts = dq[0][0] if dq else ts_sec
                end_ts = ts_sec
                
                # Обновляем кулдаун
                self.last_signal[(side, window)] = ts_sec
                
                # Создаём сигнал
                signal = Signal(
                    detector_name=self.name,
                    symbol=self.symbol,
                    side=side,
                    qty_variants=[total_qty],
                    repeats=repeats,
                    interval_avg=float(window),  # длительность окна
                    start_ts=start_ts,
                    end_ts=end_ts,
                    jitter_ms=None,
                    stability_ratio=None,
                )
                signals.append(signal)
                
                _log.info(f"[{self.symbol}] SPIKE: side={side}, window={window}s, "
                         f"qty={total_qty}, repeats={repeats}, avg={avg_volume:.1f}/s")
        
        return signals
    
    def flush(self) -> List[Signal]:
        """Возвращает незакрытые сигналы (для volume_spike — пустой список)."""
        return []
    
    def get_active_snapshot(self, now_sec: float) -> List[Dict[str, Any]]:
        """
        Возвращает snapshot активных всплесков (для GUI).
        Показывает последние сигналы за последние 30 секунд.
        """
        if not self.enabled:
            return []
        
        rows = []
        for (side, window), last_ts in self.last_signal.items():
            if now_sec - last_ts <= 30:  # показываем последние 30 секунд
                dq = self.trades_by_window[(side, window)]
                if dq:
                    total_qty = sum(q for _, q in dq)
                    repeats = len(dq)
                    start_ts = dq[0][0]
                    rows.append({
                        "symbol": self.symbol,
                        "side": side,
                        "window_sec": window,
                        "total_qty": total_qty,
                        "repeats": repeats,
                        "start_ts": start_ts,
                        "end_ts": last_ts,
                    })
        return rows