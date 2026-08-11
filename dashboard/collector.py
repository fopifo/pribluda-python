"""
Собирает текущий срез активных серий по всем тикерам и всем детекторам
каждого тикера (fast_strict, slow_strict и т.д.) в один плоский список
строк для отображения (GUI).

Только читает состояние детекторов через get_active_snapshot — ничего
не меняет, не влияет на _find_match / _prune_dead и т.д.

Здесь же — фильтрация для отображения (не для детекции!):
  - MIN_REPEATS_TO_SHOW: серии с 1-2 повторами — почти всегда случайные
    совпадения, а не настоящий робот; в таблице только шумят. Порог
    детектора (min_repeats=2) остаётся прежним — просто в таблице их не
    показываем, они по-прежнему учитываются в логике и логе.
  - Просроченные серии (seconds_to_next < 0) в таблице не помечаются
    отдельно — их просто убирают из списка. Полная информация о
    просрочке всё равно есть в файле output/live_signals_<дата>.txt.
"""

from detectors.interval_robot import IntervalRobotDetector

MIN_REPEATS_TO_SHOW = 3


def collect_rows(
    detectors: dict[str, list[IntervalRobotDetector]],
    now_ts: float,
    min_repeats_to_show: int = MIN_REPEATS_TO_SHOW,
) -> list[dict]:
    rows: list[dict] = []
    for symbol_detectors in detectors.values():
        for detector in symbol_detectors:
            for row in detector.get_active_snapshot(now_ts):
                if row["repeats"] < min_repeats_to_show:
                    continue
                if row["seconds_to_next"] is not None and row["seconds_to_next"] < 0:
                    continue
                rows.append(row)
    return rows