"""
Приблуда на python — компактный разбор НЕДЕЛЬНОГО ЖУРНАЛА СИГНАЛОВ
(файл вида data_sample/Week_trades.txt — это вывод пакетного прогона
детекторов по нескольким датам, а НЕ сырые сделки).
Философия та же, что в weekly_review.py: тяжёлая агрегация локально,
помощнику отдаётся компактный отчёт (несколько КБ), а не 129 тыс. строк.
Считает:
- суммарно по датам и пресетам;
- распределение повторов, джиттер (медиана/p90), доли стаб=100% и стаб=0%;
- топ тикеров-спамеров;
- "дубли": кластеры сигналов с одинаковым стартом (±2с) и интервалом
  (±10%) по одному тикеру/стороне — признак расщепления одного
  TWAP-потока на несколько серий (баг first-fit, чинится best-fit).
Запуск (из корня проекта):
python analysis/week_signals_review.py [путь_к_файлу]
"""
import re
import sys
import statistics
from collections import Counter, defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PATH = BASE_DIR / "data_sample" / "Week_trades.txt"

DATE_PATTERN = re.compile(r"^\s*ДАТА\s+(?P<date>\d{4}-\d{2}-\d{2})")
SIGNAL_PATTERN = re.compile(
    r"^\s*\[робот-интервал\[(?P<preset>[^\]]+)\]\]\s+"
    r"(?P<symbol>[A-Z0-9]+)\s+(?P<side>buy|sell)\s+"
    r"qty=(?P<qty>[\d-]+)\s+повторов=(?P<repeats>\d+)\s+"
    r"интервал~(?P<interval>[\d.]+)с\s+"
    r"джиттер=(?:(?P<jitter>[\d.]+)мс|н/д)"
    r"(?:\s+стаб(?:ильность)?=(?P<stab>\d+)%)?\s+"
    r"с\s+(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2})\s+по\s+"
)


def parse_time(h: str, m: str, s: str) -> float:
    return int(h) * 3600 + int(m) * 60 + float(s)


def count_dup_extra(sigs: list[tuple[float, float]]) -> int:
    """Жадная кластеризация по (старт ±2с, интервал ±10%).
    Возвращает число "лишних" сигналов (размер кластера - 1)."""
    sigs.sort()
    extra = 0
    cur_start = cur_interval = None
    cur_size = 0
    for start, interval in sigs:
        if (
            cur_start is not None
            and (start - cur_start) <= 2.0
            and abs(interval - cur_interval) <= 0.10 * cur_interval
        ):
            cur_size += 1
        else:
            if cur_size >= 2:
                extra += cur_size - 1
            cur_start, cur_interval, cur_size = start, interval, 1
    if cur_size >= 2:
        extra += cur_size - 1
    return extra


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    if not path.exists():
        print(f"Файл не найден: {path}")
        return

    current_date = "?"
    per_date = Counter()
    per_preset = defaultdict(lambda: {
        "count": 0, "repeats": Counter(), "jitters": [],
        "stab100": 0, "stab0": 0,
    })
    per_symbol = Counter()
    per_symbol_preset = defaultdict(Counter)
    dup_streams = defaultdict(list)  # (date, symbol, side) -> [(start, interval)]

    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            dm = DATE_PATTERN.match(line)
            if dm:
                current_date = dm.group("date")
                continue
            m = SIGNAL_PATTERN.match(line)
            if not m:
                continue
            preset = m.group("preset")
            symbol = m.group("symbol")
            side = m.group("side")
            repeats = int(m.group("repeats"))
            interval = float(m.group("interval"))
            jitter = float(m.group("jitter")) if m.group("jitter") else None
            stab = int(m.group("stab")) if m.group("stab") else None
            start = parse_time(m.group("sh"), m.group("sm"), m.group("ss"))

            per_date[current_date] += 1
            agg = per_preset[preset]
            agg["count"] += 1
            agg["repeats"][repeats] += 1
            if jitter is not None:
                agg["jitters"].append(jitter)
            if stab == 100:
                agg["stab100"] += 1
            elif stab == 0:
                agg["stab0"] += 1
            per_symbol[symbol] += 1
            per_symbol_preset[symbol][preset] += 1
            dup_streams[(current_date, symbol, side)].append((start, interval))

    lines = []
    lines.append("=" * 72)
    lines.append("ОТЧЁТ ПО НЕДЕЛЬНОМУ ЖУРНАЛУ СИГНАЛОВ")
    lines.append("=" * 72)
    lines.append("")
    lines.append("-- ПО ДАТАМ --")
    for date in sorted(per_date):
        lines.append(f"  {date}: {per_date[date]} сигналов")
    lines.append("")
    lines.append("-- ПО ПРЕСЕТАМ --")
    for preset, agg in sorted(per_preset.items()):
        n = agg["count"]
        lines.append(f"{preset}: {n} сигналов")
        top_rep = ", ".join(f"{r}повт:{c}" for r, c in agg["repeats"].most_common(5))
        lines.append(f"  повторов: {top_rep}")
        if agg["jitters"]:
            jj = sorted(agg["jitters"])
            med = statistics.median(jj)
            p90 = jj[int(0.9 * (len(jj) - 1))]
            lines.append(f"  джиттер: медиана {med:.0f}мс, p90 {p90:.0f}мс")
        lines.append(
            f"  стаб=100%: {agg['stab100'] / n:.0%}, стаб=0%: {agg['stab0'] / n:.0%}"
        )
    lines.append("")
    lines.append("-- ТОП-15 ТИКЕРОВ ПО ЧИСЛУ СИГНАЛОВ --")
    lines.append(f"{'ТИКЕР':<8}{'ВСЕГО':>8}{'twap':>8}{'fast':>8}")
    for symbol, total in per_symbol.most_common(15):
        pp = per_symbol_preset[symbol]
        lines.append(
            f"{symbol:<8}{total:>8}{pp.get('twap_strict', 0):>8}{pp.get('fast_strict', 0):>8}"
        )
    lines.append("")
    lines.append("-- ДУБЛИ (расщепление одного потока на несколько серий) --")
    total_extra = 0
    extra_by_symbol = Counter()
    for key, sigs in dup_streams.items():
        extra = count_dup_extra(sigs)
        if extra > 0:
            total_extra += extra
            extra_by_symbol[key[1]] += extra
    lines.append(f"Всего 'лишних' сигналов-дублей: {total_extra}")
    lines.append("Топ-10 тикеров по дублям:")
    for symbol, extra in extra_by_symbol.most_common(10):
        lines.append(f"  {symbol:<8} {extra}")

    report = "\n".join(lines)
    print(report)


if __name__ == "__main__":
    main()