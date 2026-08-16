"""
Приблуда на python — еженедельный сжатый отчёт по логам скринера.

Идея: помощник (DeepSeek) не может переварить сырые логи целиком — за
день это 30-40 тыс. строк, за неделю сотни тысяч. Поэтому вся тяжёлая
агрегация (парсинг, кластеризация TWAP, статистика джиттера) делается
здесь, локально, в коде — а помощнику отдаётся уже ГОТОВЫЙ КОМПАКТНЫЙ
ОТЧЁТ (обычно несколько КБ, не мегабайты) с выводами по каждому тикеру,
а не исходные данные для пересчёта.

Запуск раз в неделю (вручную или по расписанию, см. низ файла):
    python weekly_review.py [--days 7]

Что делает:
  1. Собирает все output/live_signals_*.txt за последние N дней.
  2. Парсит все сигналы (та же логика, что и analyze_parallel.py).
  3. Для КАЖДОГО тикера считает:
     - сколько дней из N в нём вообще были сигналы fast_strict
     - средний и разброс джиттера по fast_strict-сигналам
     - сколько дней в нём находился TWAP-паттерн (параллельные сигналы
       с одинаковым таймингом, разным объёмом) и средний размер кластера
  4. Сортирует тикеры по "интересности" (устойчивость паттерна изо дня
     в день) и пишет компактный текстовый отчёт.

Результат — output/weekly_review_<дата>.txt — именно этот файл (а не
сырые логи) стоит передавать помощнику для интерпретации/следующих
задач.
"""

import argparse
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"

START_TOLERANCE = 2.0
END_TOLERANCE = 2.0
INTERVAL_TOLERANCE_FACTOR = 0.10

SIGNAL_PATTERN = re.compile(
    r'^\[(?P<log_time>\d{2}:\d{2}:\d{2})\]\s+(?P<label>НОВЫЙ|ЗАКРЫТ)\s+'
    r'\[робот-интервал\[(?P<preset>[^\]]+)\]\]\s+(?P<symbol>[A-Z0-9]+)\s+(?P<side>buy|sell)\s+'
    r'qty=(?P<qty>[\d-]+)\s+повторов=(?P<repeats>\d+)\s+'
    r'интервал~(?P<interval>[\d.]+)с\s+'
    r'(?:джиттер=(?P<jitter>[\d.]+)мс\s+|джиттер=н/д\s+)'
    r'с (?P<start_h>\d{2}):(?P<start_m>\d{2}):(?P<start_s>\d{2}) '
    r'по (?P<end_h>\d{2}):(?P<end_m>\d{2}):(?P<end_s>\d{2}) '
    r'\(длилось [\d.]+ сек\)$'
)


@dataclass
class Signal:
    symbol: str
    side: str
    preset: str
    qty: int
    repeats: int
    interval: float
    jitter_ms: float | None
    start_sec: float
    end_sec: float


def parse_time(h: str, m: str, s: str) -> float:
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_signal_line(line: str) -> Signal | None:
    m = SIGNAL_PATTERN.match(line)
    if not m:
        return None
    qty_str = m.group("qty")
    qty = sum(int(x) for x in qty_str.split("-")) if "-" in qty_str else int(qty_str)
    jitter_raw = m.group("jitter")
    jitter_ms = float(jitter_raw) if jitter_raw else None
    return Signal(
        symbol=m.group("symbol"),
        side=m.group("side"),
        preset=m.group("preset"),
        qty=qty,
        repeats=int(m.group("repeats")),
        interval=float(m.group("interval")),
        jitter_ms=jitter_ms,
        start_sec=parse_time(m.group("start_h"), m.group("start_m"), m.group("start_s")),
        end_sec=parse_time(m.group("end_h"), m.group("end_m"), m.group("end_s")),
    )


def find_parallel_groups(signals: list[Signal]) -> list[list[Signal]]:
    """Та же логика, что в analyze_parallel.py — кластеры сигналов с
    близким start/end/интервалом (одного тикера и стороны)."""
    by_key = defaultdict(list)
    for sig in signals:
        by_key[(sig.symbol, sig.side)].append(sig)

    groups = []
    for (symbol, side), sigs in by_key.items():
        sigs = sorted(sigs, key=lambda s: s.start_sec)
        n = len(sigs)
        used = [False] * n
        for i in range(n):
            if used[i]:
                continue
            cluster = [sigs[i]]
            used[i] = True
            changed = True
            while changed:
                changed = False
                avg_start = sum(s.start_sec for s in cluster) / len(cluster)
                avg_end = sum(s.end_sec for s in cluster) / len(cluster)
                avg_interval = sum(s.interval for s in cluster) / len(cluster)
                for j in range(n):
                    if used[j]:
                        continue
                    c = sigs[j]
                    if (abs(c.start_sec - avg_start) <= START_TOLERANCE and
                        abs(c.end_sec - avg_end) <= END_TOLERANCE and
                        abs(c.interval - avg_interval) <= INTERVAL_TOLERANCE_FACTOR * avg_interval):
                        cluster.append(c)
                        used[j] = True
                        changed = True
            if len(cluster) >= 2:
                groups.append(cluster)
    return groups


@dataclass
class SymbolStats:
    days_with_fast_strict: set = field(default_factory=set)
    fast_strict_jitters: list = field(default_factory=list)
    days_with_twap: set = field(default_factory=set)
    twap_cluster_sizes: list = field(default_factory=list)


def find_log_files(days: int) -> list[tuple]:
    if not OUTPUT_DIR.exists():
        return []
    cutoff = datetime.now().date() - timedelta(days=days)
    files = []
    for path in sorted(OUTPUT_DIR.glob("live_signals_*.txt")):
        date_str = path.stem.replace("live_signals_", "")
        try:
            file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date >= cutoff:
            files.append((file_date, path))
    files.sort()
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description="Еженедельный сжатый отчёт по логам скринера")
    parser.add_argument("--days", type=int, default=7, help="За сколько последних дней анализировать")
    args = parser.parse_args()

    day_files = find_log_files(args.days)
    if not day_files:
        print(f"Не найдено логов за последние {args.days} дней в {OUTPUT_DIR}")
        return

    print(f"Найдено дней с логами: {len(day_files)}")
    stats: dict[str, SymbolStats] = defaultdict(SymbolStats)
    total_signals = 0

    for file_date, path in day_files:
        signals = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip()
                if not line:
                    continue
                sig = parse_signal_line(line)
                if sig:
                    signals.append(sig)
        total_signals += len(signals)
        print(f"  {file_date}: {len(signals)} сигналов")

        for sig in signals:
            if sig.preset != "twap_strict":
                st = stats[sig.symbol]
                st.days_with_fast_strict.add(file_date)
                if sig.jitter_ms is not None:
                    st.fast_strict_jitters.append(sig.jitter_ms)

        groups = find_parallel_groups(signals)
        # TWAP считаем по кластерам >=3 сигналов (2 слишком легко совпадают)
        for group in groups:
            if len(group) < 3:
                continue
            symbol = group[0].symbol
            st = stats[symbol]
            st.days_with_twap.add(file_date)
            st.twap_cluster_sizes.append(len(group))

    print(f"\nВсего сигналов за период: {total_signals}")

    n_days = len(day_files)

    lines = []
    lines.append("=" * 78)
    lines.append(f"ЕЖЕНЕДЕЛЬНЫЙ ОТЧЁТ — {day_files[0][0]} — {day_files[-1][0]} ({n_days} дн.)")
    lines.append("=" * 78)
    lines.append("")
    lines.append("-- УСТОЙЧИВЫЕ FAST_STRICT (низкий джиттер день ото дня — сильнее похоже на автомат) --")
    lines.append(f"{'Тикер':<8} {'Дней из ' + str(n_days):<10} {'Ср.джиттер,мс':<16} {'Мин-макс,мс':<16}")
    lines.append("-" * 78)
    fast_rows = [
        (sym, st) for sym, st in stats.items()
        if st.days_with_fast_strict and st.fast_strict_jitters
    ]
    fast_rows.sort(key=lambda x: (-len(x[1].days_with_fast_strict), statistics.mean(x[1].fast_strict_jitters)))
    for sym, st in fast_rows[:25]:
        jitters = st.fast_strict_jitters
        avg_j = statistics.mean(jitters)
        lines.append(
            f"{sym:<8} {len(st.days_with_fast_strict):<10} {avg_j:<16.1f} "
            f"{min(jitters):.1f}-{max(jitters):.1f}"
        )

    lines.append("")
    lines.append("-- TWAP-ПАТТЕРН (параллельные сигналы, одинаковый тайминг, разный объём) --")
    lines.append(f"{'Тикер':<8} {'Дней из ' + str(n_days):<10} {'Кластеров всего':<16} {'Ср.размер кластера':<20}")
    lines.append("-" * 78)
    twap_rows = [(sym, st) for sym, st in stats.items() if st.days_with_twap]
    twap_rows.sort(key=lambda x: -len(x[1].days_with_twap))
    for sym, st in twap_rows[:25]:
        sizes = st.twap_cluster_sizes
        lines.append(
            f"{sym:<8} {len(st.days_with_twap):<10} {len(sizes):<16} {statistics.mean(sizes):<20.1f}"
        )

    lines.append("")
    lines.append(f"Тикеров с TWAP-паттерном {n_days}/{n_days} дней (кандидаты на приоритетное наблюдение):")
    everyday_twap = [sym for sym, st in stats.items() if len(st.days_with_twap) == n_days]
    lines.append("  " + (", ".join(sorted(everyday_twap)) if everyday_twap else "(нет)"))

    report_text = "\n".join(lines)
    print("\n" + report_text)

    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / f"weekly_review_{datetime.now().strftime('%Y-%m-%d')}.txt"
    out_path.write_text(report_text, encoding="utf-8")
    print(f"\nКомпактный отчёт сохранён: {out_path}")
    print("Именно этот файл (не сырые логи) стоит отдавать помощнику.")


if __name__ == "__main__":
    main()