"""
Приблуда на python — прокси-импорт для совместимости.
SharedState теперь живёт в core/state.py
"""
from core.state import SharedState

__all__ = ["SharedState"]