# tests/test_interval_robot.py
"""
Модульные тесты для детектора IntervalRobotDetector.
"""

import pytest
from detectors.interval_robot import IntervalRobotDetector
from detectors.base import Signal


@pytest.fixture
def strict_config():
    """Конфигурация для строгого режима (fast_strict)."""
    return {
        "min_qty": 10,
        "min_repeats": 2,
        "min_interval": 2.0,
        "max_interval": 30.0,
        "max_qty_variants": 1,
        "interval_tolerance": 0.15,
        "preset_name": "fast_strict",
    }


@pytest.fixture
def loose_config():
    """Конфигурация для свободного режима (fast_loose)."""
    return {
        "min_qty": 10,
        "min_repeats": 2,
        "min_interval": 2.0,
        "max_interval": 30.0,
        "max_qty_variants": 2,
        "max_qty_ratio": 1.5,
        "preset_name": "fast_loose",
    }


def make_trade(qty: int, side: str, ts_sec: float) -> dict:
    """Вспомогательная функция: создаёт словарь сделки с timestamp в миллисекундах."""
    return {"qty": qty, "side": side, "timestamp": int(ts_sec * 1000)}


class TestIntervalRobotDetector:

    def test_basic_sequence_strict(self, strict_config):
        """Проверяем, что детектор находит серию из трёх сделок с одинаковым интервалом."""
        detector = IntervalRobotDetector("SBER", strict_config)
        trades = [
            make_trade(45, "buy", 0.0),
            make_trade(45, "buy", 3.0),
            make_trade(45, "buy", 6.0),
        ]
        signals = []
        for t in trades:
            signals.extend(detector.on_trade(t))
        signals.extend(detector.flush())

        # Должен быть один сигнал, repeats = 3
        assert len(signals) == 1
        s = signals[0]
        assert s.symbol == "SBER"
        assert s.side == "buy"
        assert s.qty_variants == [45]
        assert s.repeats == 3
        assert s.interval_avg == pytest.approx(3.0)

    def test_strict_tolerance(self, strict_config):
        """Проверяем, что интервал с отклонением в пределах tolerance принимается."""
        detector = IntervalRobotDetector("SBER", strict_config)
        trades = [
            make_trade(45, "buy", 0.0),
            make_trade(45, "buy", 3.0),
            make_trade(45, "buy", 6.4),   # 6.4 - 3.0 = 3.4, отклонение 13.3% (<15%)
        ]
        signals = []
        for t in trades:
            signals.extend(detector.on_trade(t))
        signals.extend(detector.flush())
        assert len(signals) == 1
        assert signals[0].repeats == 3

    def test_strict_tolerance_fails(self, strict_config):
        """Интервал выходит за tolerance – серия обрывается."""
        detector = IntervalRobotDetector("SBER", strict_config)
        trades = [
            make_trade(45, "buy", 0.0),
            make_trade(45, "buy", 3.0),
            make_trade(45, "buy", 6.5),   # 3.5 сек, отклонение 16.7% >15%
        ]
        signals = []
        for t in trades:
            signals.extend(detector.on_trade(t))
        signals.extend(detector.flush())
        # Должна быть серия из первых двух (repeats=2), а третья начала новую, но не хватило повторов.
        # После flush закроются обе: первая с repeats=2, вторая с repeats=1 (не попадает под min_repeats)
        assert len(signals) == 1
        s = signals[0]
        assert s.repeats == 2
        assert s.end_ts == 3.0

    def test_qty_variants_loose(self, loose_config):
        """Проверяем чередование объёма в свободном режиме."""
        detector = IntervalRobotDetector("SBER", loose_config)
        trades = [
            make_trade(45, "buy", 0.0),
            make_trade(46, "buy", 3.0),
            make_trade(45, "buy", 6.0),
        ]
        signals = []
        for t in trades:
            signals.extend(detector.on_trade(t))
        signals.extend(detector.flush())
        assert len(signals) == 1
        s = signals[0]
        assert set(s.qty_variants) == {45, 46}
        assert s.repeats == 3

    def test_qty_variants_strict_denied(self, strict_config):
        """В строгом режиме max_qty_variants=1, поэтому смена объёма разрывает серию."""
        detector = IntervalRobotDetector("SBER", strict_config)
        trades = [
            make_trade(45, "buy", 0.0),
            make_trade(46, "buy", 3.0),
            make_trade(45, "buy", 6.0),
        ]
        signals = []
        for t in trades:
            signals.extend(detector.on_trade(t))
        signals.extend(detector.flush())
        # Первая пара (45,46) не подходит из-за разных qty, поэтому каждая сделка начинает свою серию.
        # После flush будут закрыты все, у кого repeats >= 2, но ни одна не достигнет 2 повторов,
        # потому что каждая новая сделка не находит совпадения.
        # На самом деле первая сделка (45) создаёт кандидата, вторая (46) не подходит (qty не совпадает и max_qty_variants=1)
        # создаёт нового кандидата, третья (45) найдёт первого кандидата (интервал 6с, но у первого last_interval=None, tolerance не применяется, интервал 6с в диапазоне) - но qty совпадает, так что продлит первую серию до repeats=2.
        # Вторая серия (46) останется с repeats=1 и не будет закрыта.
        # Итог: один сигнал с repeats=2, qty_variants=[45].
        # Проверим.
        assert len(signals) == 1
        s = signals[0]
        assert s.qty_variants == [45]
        assert s.repeats == 2

    def test_timeout(self, strict_config):
        """Проверяем, что серия закрывается по таймауту max_interval."""
        detector = IntervalRobotDetector("SBER", strict_config)
        trades = [
            make_trade(45, "buy", 0.0),
            make_trade(45, "buy", 3.0),
            # следующая сделка через 35 секунд (больше max_interval=30)
            make_trade(45, "buy", 35.0),
        ]
        signals = []
        for t in trades:
            signals.extend(detector.on_trade(t))
        # После третьей сделки, при её обработке, _prune_dead закроет первую серию (repeats=2)
        # и третья сделка начнёт новую.
        signals.extend(detector.flush())
        # Должен быть сигнал от первой серии (repeats=2) и, возможно, от третьей (repeats=1, не закрывается)
        assert len(signals) == 1
        s = signals[0]
        assert s.repeats == 2
        assert s.end_ts == 3.0

    def test_check_overdue_warning_strict(self, strict_config):
        """Проверяем, что check_overdue выдаёт предупреждение при просрочке в strict режиме."""
        detector = IntervalRobotDetector("SBER", strict_config)
        trades = [
            make_trade(45, "buy", 0.0),
            make_trade(45, "buy", 3.0),
        ]
        for t in trades:
            detector.on_trade(t)

        # Теперь прошло 3.5 секунды после последней сделки (ожидаемый интервал 3.0 + 15% = 3.45)
        now = 6.5  # 0 + 3.0 + 3.5
        signals, warnings = detector.check_overdue(now)
        # Должно быть предупреждение (но не закрытие, т.к. ещё не превышен max_interval=30)
        assert len(warnings) == 1
        assert "просрочка" in warnings[0]
        assert len(signals) == 0

        # Если пройдёт больше max_interval, то check_overdue закроет серию
        now2 = 35.0
        signals2, warnings2 = detector.check_overdue(now2)
        assert len(signals2) == 1
        s = signals2[0]
        assert s.repeats == 2
        assert len(warnings2) == 0  # после закрытия предупреждение не выдаётся

    def test_max_series_length(self, strict_config):
        """Проверяем, что серия закрывается при достижении MAX_SERIES_LENGTH (20)."""
        detector = IntervalRobotDetector("SBER", strict_config)
        # Создадим 21 сделку с интервалом 3 сек
        trades = [make_trade(45, "buy", i * 3.0) for i in range(21)]
        signals = []
        for t in trades:
            signals.extend(detector.on_trade(t))
        signals.extend(detector.flush())
        # Должны получить сигнал с repeats=20 (первая серия закрылась на 20-й сделке)
        # и, возможно, новая серия началась с 21-й сделки (repeats=1, не закрывается)
        # Ищем сигнал с repeats=20
        found = [s for s in signals if s.repeats == 20]
        assert len(found) == 1
        assert found[0].end_ts == 19 * 3.0  # последняя сделка первой серии (индекс 19)

    def test_active_cap(self, strict_config):
        """Проверяем, что при превышении MAX_ACTIVE_PER_SIDE старые серии вытесняются."""
        # Установим низкий лимит для теста (переопределим атрибут класса)
        detector = IntervalRobotDetector("SBER", strict_config)
        detector.MAX_ACTIVE_PER_SIDE = 3  # уменьшаем для теста
        # Создаём много сделок с разными qty, чтобы каждая создавала нового кандидата
        # Но в strict режиме разные qty не совмещаются, поэтому каждый новый qty создаёт нового кандидата
        trades = [
            make_trade(10, "buy", 0.0),
            make_trade(20, "buy", 1.0),
            make_trade(30, "buy", 2.0),
            make_trade(40, "buy", 3.0),  # этот вызовет принудительное вытеснение
        ]
        signals = []
        for t in trades:
            signals.extend(detector.on_trade(t))
        signals.extend(detector.flush())
        # После 4-й сделки лимит превышен, должны быть закрыты некоторые серии (самые старые)
        # Но каждая серия имеет repeats=1, поэтому не будут закрыты (min_repeats=2)
        # Однако _enforce_cap удаляет кандидатов без сигналов, если они не достигли min_repeats.
        # Проверим, что активных осталось не более 3.
        total_active = sum(len(lst) for lst in detector.active.values())
        assert total_active <= 3

    def test_flush_closes_all(self, strict_config):
        """Проверяем, что flush закрывает все незакрытые серии с repeats>=2."""
        detector = IntervalRobotDetector("SBER", strict_config)
        trades = [
            make_trade(45, "buy", 0.0),
            make_trade(45, "buy", 3.0),
            make_trade(45, "sell", 5.0),  # другая сторона, отдельная серия
        ]
        for t in trades:
            detector.on_trade(t)
        signals = detector.flush()
        # Должны быть две серии: buy (repeats=2) и sell (repeats=1 – не закрывается)
        assert len(signals) == 1
        s = signals[0]
        assert s.side == "buy"
        assert s.repeats == 2