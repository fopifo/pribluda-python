"""
Приблуда на python — бэктест: «настоящий ли это робот» на 3-м ударе.
Владелец — скальпер: заходит вместе с роботом на ~3-м ударе, времени
разглядывать цифры нет. Поэтому главный вопрос бэктеста не «сколько
тиков прошла цена», а «продолжит ли серия бить» — это и есть настоящий
робот: случайное совпадение трёх сделок не даст 5-ю и 8-ю сделку с тем
же интервалом.

ВЕРСИЯ 3 (2026-08-22): поддержка Quik-ленты (data/quik_trades.csv)
как альтернативного источника данных. Автоматически выбирает:
если есть JSON-файлы для даты — использует их, иначе читает Quik-ленту.

Метрики «настоящий робот»:
- continued_5: серия доросла до >= 5 повторов
- continued_8: серия доросла до >= 8 повторов (уверенный робот)
Дополнительно: ход цены в тиках в окне WINDOW_SEC после 3-го удара
(медиана / p25 / p75 благоприятного хода) и «чистый» вход (fav>=GOOD_TICKS
и против нас <= MAX_ADVERSE_TICKS).

Ключевая разбивка — по трём осям, которые видны в момент 3-го удара:
1. ДЕФОЛТНЫЙ тикер: здесь серии с >= DEFAULT_REPEATS_MARK повторов
   появляются в >= DEFAULT_DAYS_MIN днях из всех.
2. ПАЧКА vs ОДИНОЧКА (batch / alone): см. BATCH_WINDOW_SEC,
   BATCH_MIN_NEIGHBORS.
3. ПОРТРЕТ на 3-м ударе:
   - "wall"    — долбит одну цену (spr3 >= WALL_SPR): самый ценный
                  признак, маркер реального интереса, не толпы;
   - "moving"  — уже сдвинул цену (shift3 != 0): это маркер того,
                  что толпа уже зашла, сам по себе не показатель силы робота;
   - "flat"    — стоит на месте, цену пока не двигает.

В отчёте в конце — ТОП профилей по вероятности «настоящий», чтобы
сразу видеть, на что смотреть в живой таблице.

Один проход по данным. ПРОГРЕСС печатается (flush=True).
Отчёт пишется в ОДИН файл output/entry_backtest.txt (новый поверх).

Запуск (из корня проекта):
python research/entry_backtest.py
"""
import bisect
import json
import sys
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.config import get_detector_configs
from core.ticker_settings import load_settings
from detectors.interval_robot import IntervalRobotDetector

DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
OUT_PATH = OUTPUT_DIR / "entry_backtest.txt"

WINDOW_SEC = 60.0          # окно наблюдения за ценой после 3-го удара
GOOD_TICKS = 2             # благоприятный ход, тиков
MAX_ADVERSE_TICKS = 2      # допустимый ход против, тиков
WALL_SPR = 0.8             # доля ударов в одну цену — маркер «долбит стену»
DEFAULT_REPEATS_MARK = 5   # серия >= этого числа = «робот работал»
DEFAULT_DAYS_MIN = 4       # тикер дефолтный, если робот в >= этом числе дней
TOP_PROFILES_N = 8         # сколько топ-профилей показать в конце
MIN_PROFILE_N = 30         # профиль попадает в топ только при N не меньше этого
BATCH_WINDOW_SEC = 30.0    # окно "пачки": соседние старты роботов
BATCH_MIN_NEIGHBORS = 2    # сколько ДРУГИХ роботов должно стартовать рядом


class TrackedDetector(IntervalRobotDetector):
    """Тот же детектор, но в момент 3-го повтора серии снимает портрет и
    держит ссылку на кандидата, чтобы в конце дня знать итог серии."""

    def __init__(self, symbol: str, settings: dict):
        super().__init__(symbol, settings)
        self.entries: list[dict] = []
        self._recorded: set[int] = set()

    def on_trade(self, trade: dict) -> list:
        signals = super().on_trade(trade)
        qty = trade["qty"]
        if qty >= self.min_qty:
            lst = self.active.get(trade["side"])
            if lst:
                cand = lst[-1]
                ts = trade["timestamp"] / 1000.0
                if (
                    cand.last_ts == ts
                    and cand.count == 3
                    and id(cand) not in self._recorded
                ):
                    self._recorded.add(id(cand))
                    shift3 = None
                    if cand.first_price is not None and cand.last_price is not None:
                        shift3 = cand.last_price - cand.first_price
                    spr3 = None
                    if cand.priced_hits > 0 and cand.price_counts:
                        spr3 = max(cand.price_counts.values()) / cand.priced_hits
                    self.entries.append({
                        "cand": cand,
                        "preset": self.preset_name,
                        "side": trade["side"],
                        "t3": ts,
                        "start_ts": cand.start_ts,
                        "p3": cand.last_price,
                        "shift3": shift3,
                        "spr3": spr3,
                    })
        return signals


def load_day_json(symbol: str, date_str: str) -> list[dict] | None:
    """Загружает сделки из JSON-файла для конкретной даты."""
    path = DATA_DIR / f"{symbol}_{date_str}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_day_quik(symbol: str, date_str: str) -> list[dict] | None:
    """Загружает сделки из Quik-ленты для символа (фильтрует по дате)."""
    csv_path = DATA_DIR / "quik_trades.csv"
    if not csv_path.exists():
        return None
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo
    MSK = ZoneInfo("Europe/Moscow")
    target_date = dt.strptime(date_str, "%Y-%m-%d").date()
    
    trades = []
    with open(csv_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            parts = line.strip().split(";")
            if len(parts) < 5:
                continue
            if parts[0] != symbol:
                continue
            try:
                ts = int(float(parts[4]))
                trade_dt = dt.fromtimestamp(ts / 1000, tz=MSK)
                if trade_dt.date() != target_date:
                    continue
                trades.append({
                    "symbol": parts[0],
                    "qty": int(float(parts[1])),
                    "price": float(parts[2]),
                    "side": parts[3],
                    "timestamp": ts,
                })
            except (ValueError, IndexError):
                continue
    return trades if trades else None


def load_day(symbol: str, date_str: str) -> list[dict] | None:
    """Загружает сделки: сначала пробует JSON, если нет — Quik-ленту."""
    trades = load_day_json(symbol, date_str)
    if trades is not None:
        return trades
    return load_day_quik(symbol, date_str)


def estimate_tick(prices: list[float]) -> float | None:
    uniq = sorted(set(prices))
    if len(uniq) < 2:
        return None
    return min(b - a for a, b in zip(uniq, uniq[1:]))


def price_excursion(ts_list, price_list, entry, tick, window_sec):
    """Благоприятный (fav) и противный (adv) ход цены в тиках в окне
    window_sec после 3-го удара. None, если цены нет или тик не вышел."""
    p3 = entry["p3"]
    if p3 is None or tick is None:
        return None
    i0 = bisect.bisect_right(ts_list, entry["t3"])
    i1 = bisect.bisect_right(ts_list, entry["t3"] + window_sec)
    window = price_list[i0:i1]
    if not window:
        return None
    hi, lo = max(window), min(window)
    if entry["side"] == "buy":
        return (hi - p3) / tick, (p3 - lo) / tick
    return (p3 - lo) / tick, (hi - p3) / tick


def pct(part: int, whole: int) -> str:
    return f"{part / whole:.0%}" if whole else "—"


def percentile(values: list[float], p: int) -> float | None:
    if not values:
        return None
    values = sorted(values)
    idx = min(int(len(values) * p / 100), len(values) - 1)
    return values[idx]


def portrait(entry: dict) -> str:
    spr3 = entry.get("spr3")
    shift3 = entry.get("shift3")
    if spr3 is not None and spr3 >= WALL_SPR:
        return "wall"
    if shift3 not in (None, 0):
        return "moving"
    return "flat"


PORTRAIT_LABELS = {
    "wall": "долбит стену",
    "moving": "цена уже двинулась",
    "flat": "стоит на месте",
}


def mark_batch(entries: list[dict]) -> None:
    """Для каждой записи помечает, стартовал ли этот робот в 'пачке' —
    вместе с >= BATCH_MIN_NEIGHBORS другими роботами по ДРУГИМ тикерам
    в окне BATCH_WINDOW_SEC. Использует start_ts серии (момент первого
    удара), потому что именно он характеризует 'включение'."""
    starts = [(e["start_ts"], i) for i, e in enumerate(entries)]
    starts.sort()
    n = len(starts)
    for k in range(n):
        t0, i = starts[k]
        # Ищем соседей по ДРУГИМ тикерам в окне [t0 - W, t0 + W]
        neighbors = 0
        lo = bisect.bisect_left(starts, (t0 - BATCH_WINDOW_SEC,))
        hi = bisect.bisect_right(starts, (t0 + BATCH_WINDOW_SEC, float("inf")))
        self_symbol = entries[i]["symbol"]
        for j in range(lo, hi):
            _, other_idx = starts[j]
            if other_idx == i:
                continue
            if entries[other_idx]["symbol"] != self_symbol:
                neighbors += 1
        entries[i]["batch"] = neighbors >= BATCH_MIN_NEIGHBORS
        entries[i]["neighbors"] = neighbors


def get_active_symbols(settings: dict) -> list[str]:
    """Возвращает список активных тикеров из настроек."""
    return [sym for sym, cfg in settings.items() if cfg.get("active", True)]


def qty_percentile(trades: list[dict], pct: float) -> int:
    """Процентиль по объёму сделок. pct в процентах (0-100)."""
    if not trades:
        return 1
    qtys = sorted(t["qty"] for t in trades if "qty" in t)
    if not qtys:
        return 1
    idx = int(len(qtys) * pct / 100)
    return qtys[min(idx, len(qtys) - 1)]


def main() -> None:
    settings = load_settings()
    symbols = get_active_symbols(settings)
    dates = sorted({p.name.split("_", 1)[1].replace(".json", "")
                    for p in DATA_DIR.glob("*_*.json")})
    n_days = len(dates)
    print(f"Тикеров: {len(symbols)}, дат: {n_days}", flush=True)

    all_entries: list[dict] = []
    robot_days: dict[str, set] = defaultdict(set)
    total_symbols = len(symbols)

    for date_str in dates:
        before = len(all_entries)
        for si, symbol in enumerate(symbols, 1):
            print(f"\r[{date_str}] тикер {si}/{total_symbols} {symbol}      ",
                  end="", flush=True)
            trades = load_day(symbol, date_str)
            if not trades:
                continue
            override = settings.get(symbol, {})
            manual = override.get("min_qty")
            min_qty = manual if manual is not None else qty_percentile(trades, 50)
            configs = get_detector_configs(symbol, min_qty, override)
            detectors = [TrackedDetector(symbol, cfg) for cfg in configs]
            ts_list = [t["timestamp"] / 1000.0 for t in trades]
            price_list = [t.get("price") for t in trades]
            has_prices = all(p is not None for p in price_list)
            tick = estimate_tick(
                [p for p in price_list if p is not None]) if has_prices else None
            for detector in detectors:
                for trade in trades:
                    detector.on_trade(trade)
                for entry in detector.entries:
                    cnt = entry["cand"].count
                    entry["continued_5"] = cnt >= 5
                    entry["continued_8"] = cnt >= 8
                    entry["symbol"] = symbol
                    if cnt >= DEFAULT_REPEATS_MARK:
                        robot_days[symbol].add(date_str)
                    exc = price_excursion(
                        ts_list, price_list, entry, tick, WINDOW_SEC)
                    if exc is not None:
                        entry["fav"], entry["adv"] = exc
                    all_entries.append(entry)
        print(f"\r[{date_str}] входов на 3-м ударе: {len(all_entries) - before}            ",
              flush=True)

    default_symbols = {s for s, days in robot_days.items()
                       if len(days) >= DEFAULT_DAYS_MIN}

    # Разметка пачек — по дням (пачка = в рамках одного торгового дня)
    by_day: dict[str, list[dict]] = defaultdict(list)
    for e in all_entries:
        day_key = e["start_ts"] // 86400
        by_day[int(day_key)].append(e)
    for group in by_day.values():
        mark_batch(group)

    # Агрегация: (пресет, дефолт, пачка, портрет)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for e in all_entries:
        key = (
            e["preset"],
            e["symbol"] in default_symbols,
            e["batch"],
            portrait(e),
        )
        groups[key].append(e)

    def summarize(rows: list[dict]) -> dict:
        n = len(rows)
        cont5 = sum(1 for r in rows if r["continued_5"])
        cont8 = sum(1 for r in rows if r["continued_8"])
        favs = [r["fav"] for r in rows if "fav" in r]
        clean = sum(1 for r in rows
                    if "fav" in r and r["fav"] >= GOOD_TICKS
                    and r["adv"] <= MAX_ADVERSE_TICKS)
        priced = sum(1 for r in rows if "fav" in r)
        return {
            "n": n,
            "cont5": cont5, "cont8": cont8,
            "fav_med": percentile(favs, 50),
            "fav_p25": percentile(favs, 25),
            "fav_p75": percentile(favs, 75),
            "clean": clean, "priced": priced,
        }

    lines = []
    lines.append("=" * 78)
    lines.append("БЭКТЕСТ v3: НАСТОЯЩИЙ ЛИ РОБОТ НА 3-М УДАРЕ")
    lines.append(f"окно цены {WINDOW_SEC:.0f}с | «чистый вход» = +{GOOD_TICKS} тика "
                 f"и против <= {MAX_ADVERSE_TICKS} | стена = spr>={WALL_SPR}")
    lines.append(f"дефолтный = робот в >= {DEFAULT_DAYS_MIN} из {n_days} дней")
    lines.append(f"пачка = >= {BATCH_MIN_NEIGHBORS} ДРУГИХ роботов по ДРУГИМ тикерам "
                 f"в ±{BATCH_WINDOW_SEC:.0f}с от старта")
    lines.append("=" * 78)

    # Базовые цифры по пресетам
    for preset in ("fast_strict", "twap_strict"):
        rows = [e for e in all_entries if e["preset"] == preset]
        if not rows:
            continue
        s = summarize(rows)
        lines.append("")
        lines.append(f"-- {preset}: всего входов {s['n']} --")
        lines.append(f"  продолжил до 5 ударов (возможно робот): {pct(s['cont5'], s['n'])}")
        lines.append(f"  продолжил до 8 ударов (уверенный робот): {pct(s['cont8'], s['n'])}")
        lines.append(f"  чистый вход (+{GOOD_TICKS} тика, против <= {MAX_ADVERSE_TICKS}): "
                     f"{pct(s['clean'], s['priced'])}")
        # Сколько пачкой / одиночками
        batch_n = sum(1 for r in rows if r["batch"])
        alone_n = len(rows) - batch_n
        lines.append(f"  пачкой: {batch_n}, одиночкой: {alone_n}")

    # Разбивка: пачка × портрет (по каждому пресету, без дефолтности)
    for preset in ("fast_strict", "twap_strict"):
        rows_preset = [e for e in all_entries if e["preset"] == preset]
        if not rows_preset:
            continue
        lines.append("")
        lines.append(f"-- {preset}: пачка × портрет --")
        lines.append(f"{'режим':<8} {'портрет':<22} {'N':>7} {'до5':>6} "
                     f"{'до8':>6} {'мед.ход':>8} {'чистый':>7}")
        for is_batch in (True, False):
            for port in ("wall", "moving", "flat"):
                sub = [e for e in rows_preset
                       if e["batch"] == is_batch and portrait(e) == port]
                if not sub:
                    continue
                s = summarize(sub)
                label = "пачка" if is_batch else "один"
                fav_med = f"{s['fav_med']:.0f}т" if s["fav_med"] is not None else "—"
                lines.append(
                    f"{label:<8} {PORTRAIT_LABELS[port]:<22} {s['n']:>7} "
                    f"{pct(s['cont5'], s['n']):>6} {pct(s['cont8'], s['n']):>6} "
                    f"{fav_med:>8} {pct(s['clean'], s['priced']):>7}"
                )

    # Топ профилей (все 4 оси: пресет, дефолт, пачка, портрет)
    profiles = []
    for (preset, is_def, is_batch, port), sub in groups.items():
        if len(sub) < MIN_PROFILE_N:
            continue
        s = summarize(sub)
        rate8 = s["cont8"] / s["n"]
        profiles.append((rate8, preset, is_def, is_batch, port, s))
    profiles.sort(key=lambda x: -x[0])

    lines.append("")
    lines.append("=" * 78)
    lines.append(f"ТОП-{TOP_PROFILES_N} ПРОФИЛЕЙ: где 3-й удар чаще всего НАСТОЯЩИЙ робот")
    lines.append(f"(только с N >= {MIN_PROFILE_N}; смотреть в живой таблице в первую очередь)")
    lines.append("=" * 78)
    if not profiles:
        lines.append("(нет профилей с достаточным N)")
    for rate8, preset, is_def, is_batch, port, s in profiles[:TOP_PROFILES_N]:
        def_label = "ДЕФОЛТ" if is_def else "случайн"
        batch_label = "пачка" if is_batch else "один"
        lines.append(
            f"  {preset} | {def_label} | {batch_label} | {PORTRAIT_LABELS[port]}: "
            f"настоящий(до8) = {rate8:.0%}  (N={s['n']}, до5={pct(s['cont5'], s['n'])})"
        )

    # Обратный топ: какие профили точно НЕ работают
    profiles.sort(key=lambda x: x[0])
    lines.append("")
    lines.append("=" * 78)
    lines.append(f"ТОП-{TOP_PROFILES_N} ПРОФИЛЕЙ: где 3-й удар ТОЧНО СЛАБЫЙ")
    lines.append(f"(избегать в живой таблице)")
    lines.append("=" * 78)
    for rate8, preset, is_def, is_batch, port, s in profiles[:TOP_PROFILES_N]:
        def_label = "ДЕФОЛТ" if is_def else "случайн"
        batch_label = "пачка" if is_batch else "один"
        lines.append(
            f"  {preset} | {def_label} | {batch_label} | {PORTRAIT_LABELS[port]}: "
            f"настоящий(до8) = {rate8:.0%}  (N={s['n']}, до5={pct(s['cont5'], s['n'])})"
        )

    lines.append("")
    lines.append(f"Дефолтные тикеры (робот в >= {DEFAULT_DAYS_MIN} из {n_days} дней):")
    lines.append("  " + (", ".join(sorted(default_symbols)) if default_symbols else "(нет)"))

    report = "\n".join(lines)
    print()
    print(report)
    OUTPUT_DIR.mkdir(exist_ok=True)
    OUT_PATH.write_text(report, encoding="utf-8")
    print(f"\nОтчёт сохранён: {OUT_PATH}")


if __name__ == "__main__":
    main()