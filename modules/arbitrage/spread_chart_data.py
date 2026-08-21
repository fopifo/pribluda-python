"""
Приблуда на python — данные для произвольного спред-графика (свечами).
Формула — любое арифметическое выражение с тикерами. Свечи ног —
через ticker_chart_data (честный перебор площадок TQBR/RFUD).
Спред на 1-мин разрешении, агрегируется в свечи таймфрейма.
Боллинджер (50; 2/3/4σ) — по close свечей спреда.
v2: импорты полными путями modules.arbitrage.* (работают из GUI и напрямую).
"""
import sys
from pathlib import Path
from datetime import datetime

# корень проекта в sys.path — чтобы modules.arbitrage.* резолвились отовсюду
_BASE = Path(__file__).resolve().parent.parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from modules.arbitrage.arb_spread_data import bollinger_multi
from modules.arbitrage.ticker_chart_data import (
    MSK, fetch_candles_1min, parse_begin, _days_back_for_tf,
)
from modules.arbitrage import spread_formula as sf

MIN_POINTS = 55


def build_custom_spread_data(formula, tf_minutes):
    tree, tickers = sf.parse_formula(formula)
    days_back = _days_back_for_tf(tf_minutes)

    closes_by_ticker = {}
    for ticker in tickers:
        candles_1m = fetch_candles_1min(ticker, days_back)
        if not candles_1m:
            return None
        closes_by_ticker[ticker] = {
            parse_begin(c["begin"]): c["close"] for c in candles_1m
        }

    time_sets = [set(closes_by_ticker[t].keys()) for t in tickers]
    common_times = sorted(set.intersection(*time_sets)) if time_sets else []

    spread_1m = []
    for ts in common_times:
        values = {t: closes_by_ticker[t][ts] for t in tickers}
        try:
            val = sf.eval_formula(tree, values)
        except Exception:
            continue
        if val is None or val != val:
            continue
        spread_1m.append((ts, val))

    buckets = {}
    for ts, val in spread_1m:
        epoch = int(ts.timestamp())
        bucket_start = epoch - (epoch % (tf_minutes * 60))
        buckets.setdefault(bucket_start, []).append(val)

    candles = []
    for bucket_start in sorted(buckets):
        vals = buckets[bucket_start]
        candles.append({
            "open": vals[0],
            "close": vals[-1],
            "high": max(vals),
            "low": min(vals),
            "begin": datetime.fromtimestamp(bucket_start, tz=MSK),
        })

    if len(candles) < MIN_POINTS:
        return None

    closes = [c["close"] for c in candles]
    bb = bollinger_multi(closes)
    return {
        "candles": candles,
        "bb": bb,
        "tf_minutes": tf_minutes,
        "formula": formula,
        "tickers": sorted(tickers),
    }