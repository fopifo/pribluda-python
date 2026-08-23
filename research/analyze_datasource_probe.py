"""
Приблуда на python — анализ результатов зонда probe_datasource_ticks.lua.
Считает процент расхождений сторон между OnAllTrade и CreateDataSource,
проверяет гипотезу flags % 2.
"""
import csv
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).resolve().parent.parent
PROBE_CSV = BASE_DIR / "data" / "probe_datasource.csv"


def analyze():
    if not PROBE_CSV.exists():
        print(f"Файл {PROBE_CSV} не найден. Запусти зонд probe_datasource_ticks.lua в QUIK.")
        return
    
    total = 0
    mismatches_ot_ds = 0
    mismatches_ot_flags = 0
    mismatches_ds_flags = 0
    
    ticker_stats = defaultdict(lambda: {"total": 0, "ot_ds": 0, "ot_flags": 0, "ds_flags": 0})
    
    with open(PROBE_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = row["ticker"]
            side_ot = row["side_onalltrade"]
            side_ds = row["side_datasource"]
            flags_parity = row["flags_parity"]
            
            if side_ds == "unknown":
                continue  # Пропускаем, если DataSource ещё не получил данные
            
            total += 1
            ticker_stats[ticker]["total"] += 1
            
            if side_ot != side_ds:
                mismatches_ot_ds += 1
                ticker_stats[ticker]["ot_ds"] += 1
            
            if side_ot != flags_parity:
                mismatches_ot_flags += 1
                ticker_stats[ticker]["ot_flags"] += 1
            
            if side_ds != flags_parity:
                mismatches_ds_flags += 1
                ticker_stats[ticker]["ds_flags"] += 1
    
    if total == 0:
        print("Нет данных для анализа. Зонд не собрал сделки.")
        return
    
    print("=" * 70)
    print("АНАЛИЗ ЗОНДА PROBE_DATASOURCE")
    print("=" * 70)
    print(f"Всего сделок: {total}")
    print()
    
    print("РАСХОЖДЕНИЯ МЕЖДУ ИСТОЧНИКАМИ:")
    print(f"  OnAllTrade vs DataSource: {mismatches_ot_ds} ({mismatches_ot_ds/total*100:.1f}%)")
    print(f"  OnAllTrade vs flags%2:    {mismatches_ot_flags} ({mismatches_ot_flags/total*100:.1f}%)")
    print(f"  DataSource vs flags%2:    {mismatches_ds_flags} ({mismatches_ds_flags/total*100:.1f}%)")
    print()
    
    print("ПО ТИКЕРАМ:")
    for ticker in sorted(ticker_stats.keys()):
        stats = ticker_stats[ticker]
        if stats["total"] == 0:
            continue
        print(f"  {ticker}: {stats['total']} сделок")
        print(f"    OnAllTrade vs DataSource: {stats['ot_ds']} ({stats['ot_ds']/stats['total']*100:.1f}%)")
        print(f"    OnAllTrade vs flags%2:    {stats['ot_flags']} ({stats['ot_flags']/stats['total']*100:.1f}%)")
        print(f"    DataSource vs flags%2:    {stats['ds_flags']} ({stats['ds_flags']/stats['total']*100:.1f}%)")
    
    print()
    print("ВЫВОДЫ:")
    
    # Какой источник точнее?
    if mismatches_ot_ds < 5:
        print("  ✓ OnAllTrade и DataSource почти всегда совпадают (<5% расхождений)")
        print("    → CreateDataSource не агрегирует сделки иначе")
        print("    → Проблема НЕ в источнике данных")
    else:
        print(f"  ✗ OnAllTrade и DataSource расходятся часто ({mismatches_ot_ds/total*100:.1f}%)")
        print("    → CreateDataSource агрегирует сделки иначе (схлопывает?)")
        print("    → Это может быть причиной 'рвущихся серий'")
    
    print()
    if mismatches_ot_flags < mismatches_ot_ds:
        print(f"  ✓ flags%2 точнее OnAllTrade ({mismatches_ot_flags/total*100:.1f}% vs {mismatches_ot_ds/total*100:.1f}%)")
        print("    → Гипотеза 'нечётный flags = sell' подтверждается")
    else:
        print(f"  ✗ flags%2 не точнее OnAllTrade ({mismatches_ot_flags/total*100:.1f}% vs {mismatches_ot_ds/total*100:.1f}%)")
        print("    → Гипотеза 'нечётный flags = sell' не подтверждается")
    
    print()
    print("РЕКОМЕНДАЦИИ:")
    if mismatches_ot_ds < 5 and mismatches_ot_flags > 20:
        print("  1. Источник данных (OnAllTrade vs DataSource) — НЕ проблема")
        print("  2. flags%2 — НЕ точнее текущего tick-rule")
        print("  3. Копать дальше: сверка с Алором через save_trades.py")
    elif mismatches_ot_ds > 20:
        print("  1. Попробовать CreateDataSource вместо OnAllTrade")
        print("  2. Проверить, не схлопывает ли он сделки по одной цене")
    elif mismatches_ot_flags < 10:
        print("  1. Попробовать flags%2 вместо tick-rule")
        print("  2. Проверить на большем объёме данных")
    else:
        print("  1. Оба альтернативных источника не дают явного улучшения")
        print("  2. Сверка с Алором — следующий шаг")


if __name__ == "__main__":
    analyze()