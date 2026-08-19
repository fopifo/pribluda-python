"""
Приблуда на python — диагностика ленты data/quik_trades.csv (сбор lua).
Однократный проход, без загрузки в память. Отвечает на вопросы:
1) БАГ СТОРОНЫ: есть ли в файле хоть одна buy-сделка (lua v1 из-за
   сравнения operation == "B" писал все сделки как sell).
2) ТОЧНОСТЬ ВРЕМЕНИ: доля меток с миллисекундной частью == 0
   (lua v1 писал os.time()*1000 — всегда ...000).
3) ПРОБЕЛЫ ПОТОКА: паузы > 60 c внутри торгового дня — признак того,
   что lua/Quik не успевал писать сделки (очередь колбэков).
4) ОКНА СКРИНШОТОВ: сколько сделок по ключевым тикерам попало в окна,
   когда конкурент показывал роботов (11:06, 14:42, 15:33).
Ничего не меняет, только читает. Запуск (из корня проекта):
python tools/quik_csv_diag.py
Отчёт: output/quik_csv_diag.txt — его и присылай.
"""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
CSV = BASE / "data" / "quik_trades.csv"
OUT = BASE / "output" / "quik_csv_diag.txt"
MSK = ZoneInfo("Europe/Moscow")

WINDOWS = [("11:05", "11:08"), ("14:41", "14:44"), ("15:32", "15:35")]
KEY_TICKERS = {
    "SBER", "GAZP", "TATN", "CHMF", "RUAL", "AFKS", "CNRU", "VTBR",
    "X5", "HEAD", "NVTK", "T", "SVCB", "PLZL", "RTKM", "RTKMP",
    "TATNP", "AKFB",
}
GAP_MS = 60_000


def hm_to_min(hm):
    h, m = map(int, hm.split(":"))
    return h * 60 + m


def main():
    if not CSV.exists():
        print(f"Файл не найден: {CSV}")
        return

    lines_total = 0
    parse_err = 0
    buy_n = sell_n = other_n = 0
    zero_ms_n = 0
    first_lines = []
    last_lines = []
    min_ts = max_ts = None
    prev_ts = None
    prev_date = None
    last_date = None
    gaps = []
    win_counts = {}  # (дата, номер_окна, тикер) -> [всего, buy, sell]

    with open(CSV, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            raw = line.rstrip("\n")
            if not raw.strip():
                continue
            if len(first_lines) < 5:
                first_lines.append(raw)
            last_lines.append(raw)
            if len(last_lines) > 5:
                last_lines.pop(0)
            p = raw.split(";")
            if len(p) < 5:
                parse_err += 1
                continue
            lines_total += 1
            sym = p[0]
            side = p[3]
            if side == "buy":
                buy_n += 1
            elif side == "sell":
                sell_n += 1
            else:
                other_n += 1
            try:
                ts = int(float(p[4]))
            except ValueError:
                parse_err += 1
                continue
            if ts % 1000 == 0:
                zero_ms_n += 1
            dt = datetime.fromtimestamp(ts / 1000.0, tz=MSK)
            cur_date = dt.date()
            if min_ts is None:
                min_ts = ts
            max_ts = ts

            if prev_ts is not None and cur_date == prev_date:
                d = ts - prev_ts
                if d > GAP_MS and 9 <= dt.hour < 19:
                    gaps.append((cur_date.isoformat(), dt.strftime("%H:%M:%S"),
                                 int(d // 1000)))
            prev_ts = ts
            prev_date = cur_date
            last_date = cur_date

            if sym in KEY_TICKERS:
                mins = dt.hour * 60 + dt.minute
                for wi, (a, b) in enumerate(WINDOWS):
                    if hm_to_min(a) <= mins < hm_to_min(b):
                        agg = win_counts.setdefault((cur_date, wi, sym), [0, 0, 0])
                        agg[0] += 1
                        if side == "buy":
                            agg[1] += 1
                        elif side == "sell":
                            agg[2] += 1
                        break

    rep = []
    rep.append("=" * 70)
    rep.append("ДИАГНОСТИКА QUIK CSV (сбор lua)")
    rep.append("=" * 70)
    rep.append(f"Всего строк: {lines_total}, ошибок парсинга: {parse_err}")
    rep.append(f"Стороны: buy={buy_n} sell={sell_n} other={other_n}")
    if lines_total and buy_n == 0:
        rep.append(">>> ПОДТВЕРЖДЕНО: в файле НЕТ ни одной buy-сделки (баг lua v1).")
    rep.append(f"Меток с ms-частью == 0: {zero_ms_n} из {lines_total} "
               f"({zero_ms_n / max(lines_total, 1):.0%})")
    if lines_total and zero_ms_n / lines_total > 0.99:
        rep.append(">>> ПОДТВЕРЖДЕНО: время с секундной точностью (lua v1).")
    if min_ts and max_ts:
        a = datetime.fromtimestamp(min_ts / 1000, tz=MSK)
        b = datetime.fromtimestamp(max_ts / 1000, tz=MSK)
        rep.append(f"Диапазон: {a:%Y-%m-%d %H:%M:%S} .. {b:%Y-%m-%d %H:%M:%S} MSK")
    rep.append(f"Последняя дата в файле: {last_date}")
    rep.append("")
    rep.append("-- ОКНА СКРИНШОТОВ (последняя дата), ключевые тикеры --")
    for wi, (a, b) in enumerate(WINDOWS):
        rep.append(f"Окно {a}-{b}:")
        rows = [(t, agg) for (d, i, t), agg in win_counts.items()
                if d == last_date and i == wi]
        rows.sort(key=lambda x: -x[1][0])
        if not rows:
            rep.append("  (нет сделок по ключевым тикерам)")
        for t, agg in rows:
            rep.append(f"  {t:<7} всего={agg[0]:<6} buy={agg[1]:<5} sell={agg[2]}")
        rep.append("")
    rep.append("-- ПРОБЕЛЫ ПОТОКА > 60 c (в торговое время) --")
    if gaps:
        for d, tm, sec in gaps[:50]:
            rep.append(f"  {d} {tm}: пауза {sec} c")
        if len(gaps) > 50:
            rep.append(f"  ... и ещё {len(gaps) - 50}")
    else:
        rep.append("  (пробелов > 60 c не найдено)")
    rep.append("")
    rep.append("-- Первые 5 строк файла --")
    rep.extend("  " + x for x in first_lines)
    rep.append("-- Последние 5 строк файла --")
    rep.extend("  " + x for x in last_lines)

    report = "\n".join(rep)
    print(report)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(report, encoding="utf-8")
    print(f"\nОтчёт сохранён: {OUT}")


if __name__ == "__main__":
    main()