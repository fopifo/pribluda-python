"""
tools/clean_tw_robots.py — дедупликация эталона T-Widgets.

Проблема: TW делает накопительные снимки, создавая несколько записей с разными id 
для одного и того же робота (например, count=4, потом count=8 с тем же стартом). 
Это искусственно раздувает FN при сравнении.

Решение: группировка по (ticker, side, start_ms // 60000, int(period / 10)).
Из каждой группы оставляем только одну запись с максимальным count.

Запуск: python tools/clean_tw_robots.py [YYYY-MM-DD]  # или без даты для всех файлов
"""
import sys
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"
MSK = ZoneInfo("Europe/Moscow")

def parse_time_to_ms(time_str):
    """Пытается извлечь миллисекунды из строки времени T-Widgets."""
    if not time_str:
        return 0
    # Пробуем распарсить ISO формат или подобный
    try:
        # Убираем Z или +00:00 для простоты парсинга, если есть
        clean_str = re.sub(r'[Z\+\-]\d{2}:\d{2}$', '', time_str)
        dt = datetime.fromisoformat(clean_str.replace('Z', ''))
        # Приводим к московскому времени, если нужно, но для bucket'а это не критично
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0

def process_file(file_path: Path):
    """Обрабатывает один файл, возвращает (было, стало)."""
    if not file_path.exists():
        return 0, 0

    records = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    original_count = len(records)
    if original_count == 0:
        return 0, 0

    # Группировка
    groups = defaultdict(list)
    for rec in records:
        ticker = rec.get('ticker', 'UNKNOWN')
        side = (rec.get('side') or rec.get('operationType') or '').upper()
        
        # Ищем время старта
        start_ms = rec.get('start_ms')
        if start_ms is None:
            start_ms = parse_time_to_ms(rec.get('createDttm') or rec.get('start'))
            
        period = float(rec.get('period') or rec.get('interval') or 0)
        count = int(rec.get('count') or rec.get('operationCount') or 0)
        
        # Ключ группировки: тикер, сторона, минута старта, интервал с шагом 10с
        time_bucket = start_ms // 60000
        interval_bucket = int(period / 10.0)
        
        key = (ticker, side, time_bucket, interval_bucket)
        
        # Сохраняем оригинальную запись и её count для сравнения
        groups[key].append((count, rec))

    # Оставляем только максимум по count в каждой группе
    deduped_records = []
    for key, group in groups.items():
        # Сортируем по count по убыванию и берем первый
        group.sort(key=lambda x: x[0], reverse=True)
        best_rec = group[0][1]
        deduped_records.append(best_rec)

    # Перезаписываем файл
    with open(file_path, 'w', encoding='utf-8') as f:
        for rec in deduped_records:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')

    return original_count, len(deduped_records)

def main():
    target_date = sys.argv[1] if len(sys.argv) > 1 else None
    
    print("=" * 70)
    print(" ДЕДУПЛИКАЦИЯ ЭТАЛОНА T-WIDGETS")
    print("=" * 70)
    
    if target_date:
        files_to_process = [DATA_DIR / f"tw_robots_{target_date}.jsonl"]
    else:
        files_to_process = list(DATA_DIR.glob("tw_robots_*.jsonl"))
        
    if not files_to_process:
        print("Файлы tw_robots_*.jsonl не найдены.")
        return

    total_before = 0
    total_after = 0

    for fpath in files_to_process:
        print(f"Обработка: {fpath.name}...", end=" ")
        before, after = process_file(fpath)
        total_before += before
        total_after += after
        removed = before - after
        pct = (removed / before * 100) if before > 0 else 0
        print(f"было: {before:5d} -> стало: {after:5d} (удалено дублей: {removed:4d}, {pct:.1f}%)")

    print("=" * 70)
    print(f" ИТОГО: было {total_before} записей -> стало {total_after} записей")
    print(f" Удалено искусственных дублей: {total_before - total_after}")
    print("=" * 70)
    print("Теперь метрики Recall при сравнении будут честными.")

if __name__ == "__main__":
    main()