"""
Модульные тесты для детектора LevelBreakDetector (пробои и подходы к уровням).
"""
import pytest
from modules.level_break import LevelBreakDetector, LevelSignal


def make_trade(price: float, ts_sec: float, qty: int = 10, side: str = "buy") -> dict:
    return {"qty": qty, "side": side, "price": price, "timestamp": int(ts_sec * 1000)}


def cfg() -> dict:
    return {
        "levels": {"resistance": [100.0], "support": [90.0]},
        "approach_points": 1.0,
        "cooldown_sec": 10,
    }


class TestLevelBreakDetector:
    def test_break_up(self):
        d = LevelBreakDetector("SBER", cfg())
        d.on_trade(make_trade(99.0, 0))
        sigs = d.on_trade(make_trade(100.5, 1))
        assert len(sigs) == 1
        assert sigs[0].event == "break_up"
        assert sigs[0].level == 100.0
        assert sigs[0].side == "buy"

    def test_break_down(self):
        d = LevelBreakDetector("SBER", cfg())
        d.on_trade(make_trade(91.0, 0))
        sigs = d.on_trade(make_trade(89.5, 1))
        assert len(sigs) == 1
        assert sigs[0].event == "break_down"
        assert sigs[0].level == 90.0
        assert sigs[0].side == "sell"

    def test_approach_up(self):
        d = LevelBreakDetector("SBER", cfg())
        d.on_trade(make_trade(98.0, 0))
        sigs = d.on_trade(make_trade(99.5, 1))  # в зоне [99, 100)
        assert any(s.event == "approach_up" for s in sigs)

    def test_approach_down(self):
        d = LevelBreakDetector("SBER", cfg())
        d.on_trade(make_trade(92.0, 0))
        sigs = d.on_trade(make_trade(90.5, 1))  # в зоне (90, 91]
        assert any(s.event == "approach_down" for s in sigs)

    def test_no_signal_far_from_levels(self):
        d = LevelBreakDetector("SBER", cfg())
        d.on_trade(make_trade(95.0, 0))
        sigs = d.on_trade(make_trade(95.5, 1))
        assert sigs == []

    def test_cooldown_blocks_repeat(self):
        d = LevelBreakDetector("SBER", cfg())
        d.on_trade(make_trade(98.0, 0))
        s1 = d.on_trade(make_trade(99.5, 1))
        assert any(s.event == "approach_up" for s in s1)
        d.on_trade(make_trade(98.0, 2))
        s2 = d.on_trade(make_trade(99.5, 3))  # внутри кулдауна 10 c
        assert not any(s.event == "approach_up" for s in s2)

    def test_flush_empty(self):
        d = LevelBreakDetector("SBER", cfg())
        assert d.flush() == []