"""
Собирает текущий срез активных серий по всем тикерам и всем детекторам
каждого тикера (fast_strict, twap_strict и т.д.) в один плоский список
строк для отображения (GUI).

Только читает состояние детекторов через get_active_snapshot — ничего
не меняет, не влияет на _find_match / _prune_dead и т.д.

Фильтрация для отображения:
  - Для обычных пресетов показываем серии с повторами >= MIN_REPEATS_TO_SHOW.
  - Для TWAP-пресета порог выше: MIN_REPEATS_TO_SHOW_TWAP, потому что
    в TWAP объём не фильтрует случайные совпадения, и серии с 2-3
    повторами почти всегда шум.
"""

from detectors.interval_robot import IntervalRobotDetector

MIN_REPEATS_TO_SHOW = 3
MIN_REPEATS_TO_SHOW_TWAP = 4


def collect_rows(
    detectors: dict[str, list[IntervalRobotDetector]],
    now_ts: float,
    min_repeats_to_show: int = MIN_REPEATS_TO_SHOW,
    min_repeats_to_show_twap: int = MIN_REPEATS_TO_SHOW_TWAP,
) -> list[dict]:
    rows: list[dict] = []
    for symbol_detectors in detectors.values():
        for detector in symbol_detectors:
            for row in detector.get_active_snapshot(now_ts):
                preset = row.get("preset", "")
                threshold = min_repeats_to_show_twap if preset == "twap_strict" else min_repeats_to_show
                if row["repeats"] < threshold:
                    continue
                if row["seconds_to_next"] is not None and row["seconds_to_next"] < 0:
                    continue
                rows.append(row)
    return rows