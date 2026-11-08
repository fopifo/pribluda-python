"""
Сравнение наших обнаруженных роботов с эталоном Aniscan.
Фильтрует наши данные по датам Aniscan и отбрасывает интервалы > 60с 
(так как Aniscan фокусируется на активных роботах).
"""
import json
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime

BASE = Path(__file__).resolve().parent.parent


def load_jsonl(filepath):
    """Загружает JSONL, игнорируя пустые и битые строки."""
    records = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def extract_date_from_record(record):
    """Извлекает дату (YYYY-MM-DD) из записи."""
    if 'createDttm' in record:
        return record['createDttm'][:10]
    if 'start_ts' in record:
        return datetime.fromtimestamp(record['start_ts']).strftime('%Y-%m-%d')
    if 'start_ms' in record:
        return datetime.fromtimestamp(record['start_ms'] / 1000).strftime('%Y-%m-%d')
    return None


def match_robots(ours, aniscan):
    """Сопоставляет наших роботов с Aniscan."""
    aniscan_by_key = defaultdict(list)
    for a in aniscan:
        key = (a['ticker'], a['operationType'].upper())
        aniscan_by_key[key].append(a)

    matched_aniscan_ids = set()
    tp = 0
    fp_details = []

    for o in ours:
        symbol = o['symbol']
        side = o['side'].upper()
        interval = float(o['interval_avg'])
        repeats = int(o['repeats'])

        # ФИЛЬТР: Игнорируем очень медленные паттерны (> 60 сек), 
        # так как Aniscan их не считает роботами.
        if interval > 60.0:
            continue

        candidates = aniscan_by_key.get((symbol, side), [])
        found = False

        for a in candidates:
            if a['id'] in matched_aniscan_ids:
                continue

            period = float(a['period'])
            op_count = int(a['operationCount'])

            # Допуск по интервалу: ±20% или ±2 секунды
            if abs(interval - period) > max(2.0, period * 0.2):
                continue

            # Допуск по количеству операций: ±2
            if abs(repeats - op_count) > 2:
                continue

            found = True
            matched_aniscan_ids.add(a['id'])
            break

        if found:
            tp += 1
        else:
            fp_details.append(f"{symbol} {side} int={interval:.1f}s reps={repeats}")

    # FN считаем только среди тех роботов Aniscan, у которых period <= 60s
    aniscan_active_count = sum(1 for a in aniscan if float(a['period']) <= 60.0)
    fn = aniscan_active_count - len(matched_aniscan_ids)
    
    # FP считаем только среди наших отфильтрованных (interval <= 60s)
    ours_active_count = sum(1 for o in ours if float(o['interval_avg']) <= 60.0)
    fp = ours_active_count - tp

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0

    return {
        'tp': tp, 'fp': fp, 'fn': fn,
        'precision': precision, 'recall': recall,
        'ours_total': len(ours),
        'ours_active': ours_active_count,
        'aniscan_total': len(aniscan),
        'aniscan_active': aniscan_active_count,
        'fp_details': fp_details[:10]
    }


def main():
    ours_file = BASE / "data" / "replay_alor_history.jsonl"
    if not ours_file.exists():
        ours_file = BASE / "data" / "robots_history.jsonl"

    date_arg = sys.argv[1] if len(sys.argv) > 1 else "2026-09-07"
    aniscan_file = BASE / "data" / f"aniscan_robots_{date_arg}.jsonl"

    if not ours_file.exists():
        print(f"❌ Не найден файл наших роботов: {ours_file}")
        return
    if not aniscan_file.exists():
        print(f"❌ Не найден файл Aniscan: {aniscan_file}")
        print(f"💡 Попробуй: python tools/aniscan_robot_history.py {date_arg}")
        return

    print(f"[*] Загрузка эталона Aniscan из {aniscan_file.name}...")
    aniscan = load_jsonl(aniscan_file)
    
    aniscan_dates = set()
    for a in aniscan:
        d = extract_date_from_record(a)
        if d:
            aniscan_dates.add(d)
            
    print(f"[*] Даты в файле Aniscan: {sorted(list(aniscan_dates))}")

    print(f"[*] Загрузка наших роботов из {ours_file.name}...")
    all_ours = load_jsonl(ours_file)
    
    ours_filtered = []
    for o in all_ours:
        d = extract_date_from_record(o)
        if d in aniscan_dates:
            ours_filtered.append(o)
            
    print(f"[*] Отфильтровано наших роботов по датам: {len(ours_filtered)} из {len(all_ours)}")
    print(f"[*] Сравнение (учитываются только интервалы <= 60с)...")
    
    metrics = match_robots(ours_filtered, aniscan)

    print("\n" + "=" * 65)
    print(" 📊 ЧЕСТНЫЕ РЕЗУЛЬТАТЫ СРАВНЕНИЯ С ЭТАЛОНОМ ANISCAN")
    print("=" * 65)
    print(f"Всего наших роботов (за эти даты):  {metrics['ours_total']}")
    print(f"Из них активных (int <= 60s):       {metrics['ours_active']}")
    print(f"Всего роботов Aniscan:              {metrics['aniscan_total']}")
    print(f"Из них активных (period <= 60s):    {metrics['aniscan_active']}")
    print("-" * 65)
    print(f"True Positives (TP):      {metrics['tp']}  (нашли правильно)")
    print(f"False Positives (FP):     {metrics['fp']}  (нашли, а Aniscan нет)")
    print(f"False Negatives (FN):     {metrics['fn']}  (Aniscan нашел, а мы нет)")
    print("-" * 65)
    print(f"🎯 Precision (Точность):  {metrics['precision']:.2%}")
    print(f"🎯 Recall (Полнота):      {metrics['recall']:.2%}")
    print("=" * 65)

    if metrics['fp_details']:
        print("\n⚠️ Примеры наших активных роботов, которых нет в Aniscan (FP):")
        for detail in metrics['fp_details']:
            print(f"   • {detail}")
    else:
        print("\n✅ Отлично! Все наши активные роботы совпали с Aniscan (FP=0).")


if __name__ == "__main__":
    main()