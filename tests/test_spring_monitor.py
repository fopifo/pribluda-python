"""
Модульные тесты для детектора SpringMonitor (спред тикера относительно IMOEXF).
"""
import pytest
from modules.arbitrage.spring_monitor import SpringMonitor


class TestSpringMonitor:
    def test_divergence_up(self):
        """Проверяем сигнал при отклонении спреда вверх."""
        m = SpringMonitor("SBER", threshold=0.5, half_life_sec=600)
        m.update_index(100.0)  # IMOEX = 100
        
        # Нормальный спред: 0% (SBER=100)
        m.on_trade("SBER", 100.0, 0)
        
        # Отклонение: +1% (SBER=101) — выше порога 0.5%
        sig = m.on_trade("SBER", 101.0, 1)
        assert sig is not None
        assert sig.kind == "divergence"
        assert sig.spread_pct == pytest.approx(1.0, abs=0.01)
        assert sig.deviation_pct >= 0.5

    def test_divergence_down(self):
        """Проверяем сигнал при отклонении спреда вниз."""
        m = SpringMonitor("SBER", threshold=0.5, half_life_sec=600)
        m.update_index(100.0)
        
        # Нормальный спред: 0%
        m.on_trade("SBER", 100.0, 0)
        
        # Отклонение: -1% (SBER=99)
        sig = m.on_trade("SBER", 99.0, 1)
        assert sig is not None
        assert sig.kind == "divergence"
        assert sig.spread_pct == pytest.approx(-1.0, abs=0.01)
        assert sig.deviation_pct <= -0.5

    def test_convergence(self):
        """Проверяем сигнал схождения после divergence."""
        m = SpringMonitor("SBER", threshold=0.5, half_life_sec=600)
        m.update_index(100.0)
        
        # Нормальный спред
        m.on_trade("SBER", 100.0, 0)
        
        # Отклонение (trigger divergence)
        m.on_trade("SBER", 101.0, 1)
        assert m.triggered
        
        # Возврат к норме (SBER=100.2, спред=+0.2% < threshold*0.5=0.25%)
        sig = m.on_trade("SBER", 100.2, 2)
        assert sig is not None
        assert sig.kind == "convergence"
        assert not m.triggered

    def test_no_signal_below_threshold(self):
        """Проверяем, что ниже порога нет сигнала."""
        m = SpringMonitor("SBER", threshold=0.5, half_life_sec=600)
        m.update_index(100.0)
        m.on_trade("SBER", 100.0, 0)
        
        # Малое отклонение: +0.3% (ниже порога 0.5%)
        sig = m.on_trade("SBER", 100.3, 1)
        assert sig is None
        assert not m.triggered

    def test_hysteresis_hold(self):
        """Проверяем гистерезис: после divergence нет новых сигналов до convergence."""
        m = SpringMonitor("SBER", threshold=0.5, half_life_sec=600)
        m.update_index(100.0)
        m.on_trade("SBER", 100.0, 0)
        
        # Divergence
        m.on_trade("SBER", 101.0, 1)
        assert m.triggered
        
        # Ещё выше (но уже triggered — тишина)
        sig = m.on_trade("SBER", 101.5, 2)
        assert sig is None
        assert m.triggered

    def test_no_index_no_signal(self):
        """Проверяем, что без IMOEXF нет сигналов."""
        m = SpringMonitor("SBER", threshold=0.5, half_life_sec=600)
        # IMOEXF не обновлён
        sig = m.on_trade("SBER", 100.0, 0)
        assert sig is None

    def test_snapshot(self):
        """Проверяем snapshot для GUI."""
        m = SpringMonitor("SBER", threshold=0.5, half_life_sec=600)
        m.update_index(100.0)
        m.on_trade("SBER", 100.0, 0)
        
        snap = m.snapshot(now_ts=1.0)
        assert snap["ticker"] == "SBER"
        assert snap["price_index"] == 100.0
        assert snap["spread_pct"] is not None