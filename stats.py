"""
Приблуда на python — общие статистические утилиты.
"""


def percentile(sorted_values: list[int], pct: float) -> int:
    """pct в диапазоне 0-100. Ожидает уже отсортированный список чисел."""
    if not sorted_values:
        return 0
    idx = min(int(len(sorted_values) * pct / 100), len(sorted_values) - 1)
    return sorted_values[idx]


def qty_percentile(trades: list[dict], pct: float) -> int:
    """Считает pct-й процентиль объёма (qty, в лотах) по списку сделок."""
    qtys = sorted(t["qty"] for t in trades)
    return percentile(qtys, pct)