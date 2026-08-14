"""
Собирает текущий срез активных серий по всем тикерам и всем детекторам
каждого тикера (fast_strict, twap_strict и т.д.) в один плоский список
строк для отображения (GUI).

Только читает состояние детекторов через get_active_snapshot — ничего
не меняет, не влияет на _find_match / _prune_dead и т.д.

Фильтрация для отображения (настраивается в GUI):
  - min_repeats_to_show      — минимальное число повторов для показа
  - max_jitter_ms            — максимальный джиттер (мс) для показа
  - max_cv_pct               — максимальный коэффициент вариации, %

Для TWAP-пресета порог повторов по умолчанию выше (4), но он может быть
изменён через GUI.
"""

from detectors.interval_robot import IntervalRobotDetector

MIN_REPEATS_TO_SHOW = 3
MIN_REPEATS_TO_SHOW_TWAP = 4


def collect_rows(
    detectors: dict[str, list[IntervalRobotDetector]],
    now_ts: float,
    min_repeats_to_show: int = MIN_REPEATS_TO_SHOW,
    min_repeats_to_show_twap: int = MIN_REPEATS_TO_SHOW_TWAP,
    max_jitter_ms: float | None = None,
    max_cv_pct: float | None = None,
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

                # Фильтр по джиттеру
                if max_jitter_ms is not None and row.get("jitter_ms") is not None:
                    if row["jitter_ms"] > max_jitter_ms:
                        continue

                # Фильтр по CV%
                if max_cv_pct is not None and row.get("jitter_ms") is not None and row.get("interval"):
                    cv = (row["jitter_ms"] / 1000.0) / row["interval"] * 100.0
                    if cv > max_cv_pct:
                        continue

                rows.append(row)
    return rows