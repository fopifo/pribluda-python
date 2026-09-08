"""
modules/batch_detector.py — обнаружение "пачки": несколько разных
тикеров бьют в одну и ту же миллисекунду (признак синхронного запуска
нескольких роботов одним внешним триггером/оператором).
Читает готовые сигналы StreamGrid (с полем ms_hits), ничего не меняет
в самом детекторе.
"""
from collections import defaultdict

# Насколько близко должны совпасть последние миллисекундные остатки
# разных тикеров, чтобы считать это одной "пачкой".
BATCH_MS_TOL = 30.0
# Окно по времени (мс) — сигналы должны прийти близко друг к другу.
BATCH_TIME_WINDOW_MS = 2000


def find_batches(recent_signals):
    """recent_signals: список сигналов StreamGrid за последние
    BATCH_TIME_WINDOW_MS. Возвращает список пачек — каждая: список
    тикеров с почти одинаковым последним ms_hits."""
    if len(recent_signals) < 2:
        return []

    groups = defaultdict(list)
    for sig in recent_signals:
        hits = sig.get("ms_hits") or []
        if not hits:
            continue
        last_ms = hits[-1]
        # Округляем до ближайшего BATCH_MS_TOL — сигналы в одной
        # "корзине" считаются потенциальной пачкой.
        bucket_key = round(last_ms / BATCH_MS_TOL)
        groups[bucket_key].append(sig)

    batches = []
    for bucket_key, sigs in groups.items():
        tickers = {s["ticker"] for s in sigs}
        if len(tickers) >= 2:
            batches.append({
                "ms_bucket": bucket_key * BATCH_MS_TOL,
                "tickers": sorted(tickers),
                "signals": sigs,
            })
    return batches