"""
Приблуда на python — данные для спред-графика арбитража.
Источник свечей — MOEX ISS candles (interval=1, 1-минутные), формат
подтверждён зондом tools/probe_iss_candles.py (Алор свечи через REST не
отдаёт — 404, поэтому ISS). Нужные таймфреймы 5/15/30/60 мин получаем
агрегацией 1-минутных свечей.
Спред считается по режиму связки из arb_pairs.json:
- absolute_rub: price_a - price_b (разница в рублях)
- ratio_pct:    price_a / price_b (отношение)
Полосы Боллинджера: период 50, три ленты ±2σ, ±3σ, ±4σ
(настройки пользователя от 2026-08-17).
Время ISS — московское; парсим его как MSK, чтобы агрегация таймфреймов
не зависела от часового пояса машины.
"""
import statistics
import time
import requests
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))

CANDLES_URL = (
    "https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR"
    "/securities/{symbol}/candles.json"
)

BB_PERIOD = 50
BB_SIGMAS = [2, 3, 4]
TIMEFRAMES_MIN = [1, 5, 15, 30, 60]

def parse_begin(begin_str):
    """'2026-08-17 10:00:00' (MSK) -> datetime с tzinfo=MSK."""
    return datetime.strptime(begin_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=MSK)

def fetch_candles_1min(symbol, days_back=12):
    """1-минутные свечи за последние days_back дней. ISS отдаёт до 500
    свечей за запрос — пагинируем параметром start."""
    till = datetime.now(MSK)
    frm = till - timedelta(days=days_back)
    candles = []
    start = 0
    while True:
        params = {
            "interval": 1,
            "from": frm.strftime("%Y-%m-%d"),
            "till": till.strftime("%Y-%m-%d"),
            "start": start,
            "iss.meta": "off",
        }
        resp = requests.get(CANDLES_URL.format(symbol=symbol), params=params, timeout=15)
        resp.raise_for_status()
        block = resp.json().get("candles", {})
        cols = block.get("columns", [])
        data = block.get("data", [])
        if not cols or not data:
            break
        idx = {c: i for i, c in enumerate(cols)}
        for row in data:
            try:
                candles.append({
                    "open": row[idx["open"]],
                    "close": row[idx["close"]],
                    "high": row[idx["high"]],
                    "low": row[idx["low"]],
                    "volume": row[idx["volume"]],
                    "begin": row[idx["begin"]],
                })
            except (KeyError, IndexError):
                continue
        if len(data) < 500:
            break
        start += len(data)
        time.sleep(0.2)
    return candles

def aggregate_to_tf(candles_1min, tf_minutes):
    """Агрегация 1-минутных свечей в таймфрейм tf_minutes."""
    if tf_minutes == 1:
        result = []
        for c in candles_1min:
            cc = dict(c)
            cc["begin"] = parse_begin(c["begin"])
            result.append(cc)
        return result
    buckets = {}
    for c in candles_1min:
        ts = parse_begin(c["begin"])
        epoch = int(ts.timestamp())
        bucket_start = epoch - (epoch % (tf_minutes * 60))
        buckets.setdefault(bucket_start, []).append(c)
    result = []
    for bucket_start in sorted(buckets):
        group = buckets[bucket_start]
        result.append({
            "open": group[0]["open"],
            "close": group[-1]["close"],
            "high": max(g["high"] for g in group),
            "low": min(g["low"] for g in group),
            "volume": sum(g["volume"] for g in group),
            "begin": datetime.fromtimestamp(bucket_start, tz=MSK),
        })
    return result

def compute_spread_series(candles_a, candles_b, mode):
    """Спред по общим временным точкам (close свечи таймфрейма)."""
    map_b = {c["begin"]: c["close"] for c in candles_b}
    spread = []
    for ca in candles_a:
        cb_close = map_b.get(ca["begin"])
        if cb_close is None:
            continue
        a = ca["close"]
        b = cb_close
        if mode == "absolute_rub":
            value = a - b
        else:  # ratio_pct
            if b == 0:
                continue
            value = a / b
        spread.append({"begin": ca["begin"], "value": value})
    return spread

def bollinger_multi(values, period=BB_PERIOD, sigmas=BB_SIGMAS):
    """SMA + три ленты Боллинджера. Для первых period-1 точек — None."""
    n = len(values)
    result = {"sma": [None] * n}
    for s in sigmas:
        result[f"upper_{s}"] = [None] * n
        result[f"lower_{s}"] = [None] * n
    for i in range(period - 1, n):
        window = values[i - period + 1:i + 1]
        m = statistics.fmean(window)
        sd = statistics.pstdev(window)
        result["sma"][i] = m
        for s in sigmas:
            result[f"upper_{s}"][i] = m + s * sd
            result[f"lower_{s}"][i] = m - s * sd
    return result

def _days_back_for_tf(tf_minutes):
    """Сколько дней истории взять, чтобы получилось ~300 свечей таймфрейма
    (торги ~480 минут в день)."""
    days = (300 * tf_minutes) // 480 + 2
    return max(2, min(days, 60))

def build_spread_chart_data(symbol_a, symbol_b, mode, tf_minutes):
    """Полные данные для графика спреда. None, если данных недостаточно."""
    days_back = _days_back_for_tf(tf_minutes)
    candles_a_1m = fetch_candles_1min(symbol_a, days_back)
    candles_b_1m = fetch_candles_1min(symbol_b, days_back)
    if not candles_a_1m or not candles_b_1m:
        return None
    candles_a_tf = aggregate_to_tf(candles_a_1m, tf_minutes)
    candles_b_tf = aggregate_to_tf(candles_b_1m, tf_minutes)
    spread_series = compute_spread_series(candles_a_tf, candles_b_tf, mode)
    if len(spread_series) < BB_PERIOD + 5:
        return None
    values = [s["value"] for s in spread_series]
    begins = [s["begin"] for s in spread_series]
    bb = bollinger_multi(values)
    return {
        "begins": begins,
        "spread": values,
        "bb": bb,
        "tf_minutes": tf_minutes,
        "mode": mode,
        "symbol_a": symbol_a,
        "symbol_b": symbol_b,
    }