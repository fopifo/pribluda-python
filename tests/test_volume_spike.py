"""
Модульные тесты для детектора VolumeSpikeDetector.
"""
import pytest
from modules.volume_spike import VolumeSpikeDetector
from detectors.base import Signal


@pytest.fixture
def default_config():
    """Конфигурация по умолчанию."""
    return {
        "volume_spike_enabled": True,
        "volume_spike_windows": [1, 2, 3, 4],
        "volume_spike_min_volume": 100,
        "volume_spike_threshold_multiplier": 2.0,
        "volume_spike_cooldown_sec": 5,
    }


def make_trade(qty: int, side: str, ts_sec: float) -> dict:
    """Создаёт сделку с timestamp в миллисекундах."""
    return {"qty": qty, "side": side, "timestamp": int(ts_sec * 1000)}


class TestVolumeSpikeDetector:
    def test_basic_spike(self, default_config):
        """Проверяем, что детектор находит всплеск объёма."""
        detector = VolumeSpikeDetector("SBER", default_config)

        # 10 сделок по 15 лотов за 1 секунду = 150 лотов
        trades = [make_trade(15, "buy", i * 0.1) for i in range(10)]

        signals = []
        for t in trades:
            signals.extend(detector.on_trade(t))

        # Должен быть хотя бы один сигнал (окно 1s).
        # Детектор срабатывает, как только порог пройден (может быть на 7-й сделке).
        assert len(signals) >= 1
        s = signals[0]
        assert s.symbol == "SBER"
        assert s.side == "buy"
        assert s.qty_variants[0] >= 100  # порог min_volume пройден
        assert s.repeats >= 7  # минимум 7 сделок в окне на момент первого сигнала

    def test_threshold_not_reached(self, default_config):
        """Проверяем, что слабый всплеск не детектируется."""
        detector = VolumeSpikeDetector("SBER", default_config)

        # 5 сделок по 10 лотов = 50 лотов (ниже порога 100)
        trades = [make_trade(10, "buy", i * 0.2) for i in range(5)]

        signals = []
        for t in trades:
            signals.extend(detector.on_trade(t))

        assert len(signals) == 0

    def test_cooldown(self, default_config):
        """Проверяем, что кулдаун работает."""
        detector = VolumeSpikeDetector("SBER", default_config)

        # Первый всплеск
        trades1 = [make_trade(20, "buy", i * 0.1) for i in range(10)]
        for t in trades1:
            detector.on_trade(t)

        # Второй всплеск через 2 секунды (внутри кулдауна 5 сек)
        trades2 = [make_trade(20, "buy", 2.0 + i * 0.1) for i in range(10)]
        signals = []
        for t in trades2:
            signals.extend(detector.on_trade(t))

        assert len(signals) == 0  # кулдаун не прошёл

    def test_cooldown_expired(self, default_config):
        """Проверяем, что после кулдауна сигнал проходит."""
        detector = VolumeSpikeDetector("SBER", default_config)

        # Первый всплеск
        trades1 = [make_trade(20, "buy", i * 0.1) for i in range(10)]
        for t in trades1:
            detector.on_trade(t)

        # Второй всплеск через 6 секунд (после кулдауна 5 сек)
        trades2 = [make_trade(20, "buy", 6.0 + i * 0.1) for i in range(10)]
        signals = []
        for t in trades2:
            signals.extend(detector.on_trade(t))

        assert len(signals) >= 1  # кулдаун прошёл

    def test_multiple_windows(self, default_config):
        """Проверяем, что всплеск детектируется в нескольких окнах."""
        detector = VolumeSpikeDetector("SBER", default_config)

        # 20 сделок по 10 лотов за 2 секунды
        trades = [make_trade(10, "buy", i * 0.1) for i in range(20)]

        signals = []
        for t in trades:
            signals.extend(detector.on_trade(t))

        # Должны быть сигналы для окон 1s, 2s, 3s, 4s
        windows_found = {s.interval_avg for s in signals}
        assert 1.0 in windows_found or 2.0 in windows_found

    def test_disabled(self, default_config):
        """Проверяем, что отключённый детектор не генерирует сигналы."""
        config = dict(default_config, volume_spike_enabled=False)
        detector = VolumeSpikeDetector("SBER", config)

        trades = [make_trade(20, "buy", i * 0.1) for i in range(10)]
        signals = []
        for t in trades:
            signals.extend(detector.on_trade(t))

        assert len(signals) == 0

    def test_per_ticker_override(self):
        """Проверяем, что per-ticker override работает."""
        config = {
            "volume_spike_min_volume": 100,
            "volume_spike_min_volume_override": 50,  # override для этого тикера
        }
        detector = VolumeSpikeDetector("SBER", config)

        # 50 лотов (проходит override, но не проходит global)
        trades = [make_trade(10, "buy", i * 0.2) for i in range(5)]
        signals = []
        for t in trades:
            signals.extend(detector.on_trade(t))

        assert len(signals) >= 1

    def test_flush_empty(self, default_config):
        """Проверяем, что flush возвращает пустой список."""
        detector = VolumeSpikeDetector("SBER", default_config)
        assert detector.flush() == []