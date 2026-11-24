"""
tools/weekly_cleanup.py — еженедельная очистка мусора.
Удаляет сырые данные старше 7 дней, оставляя только актуальную неделю,
агрегаты (robots_history.jsonl) и конфигурации.
"""
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
MSK = ZoneInfo("Europe/Moscow")
DAYS_TO_KEEP = 7

# Паттерны файлов, которые можно безопасно удалять по дате
PATTERNS = [
    "live_trades_*.jsonl",
    "pbsdk_trades_*.jsonl",
    "orderbook_*.jsonl",
    "aniscan_robots_*.jsonl",
    "*_202*.json",       # дневные файлы Алора
    "positions_*.json",  # снапшоты позиций (для фандинга)
    "funding_*.json",    # дневные файлы фандинга
]


def main():
    print("=" * 70)
    print(" ЕЖЕНЕДЕЛЬНАЯ ОЧИСТКА (удаление данных старше 7 дней)")
    print("=" * 70)

    now = datetime.now(MSK)
    cutoff = now - timedelta(days=DAYS_TO_KEEP)

    deleted_count = 0
    deleted_size = 0

    for pattern in PATTERNS:
        for file_path in DATA.glob(pattern):
            try:
                mtime = datetime.fromtimestamp(file_path.stat().st_mtime, MSK)
                if mtime < cutoff:
                    size = file_path.stat().st_size
                    file_path.unlink()
                    deleted_count += 1
                    deleted_size += size
                    age_days = (now - mtime).days
                    size_mb = size / 1024 / 1024
                    print(f"[-] Удалён: {file_path.name} ({size_mb:.1f} МБ, возраст: {age_days} дн.)")
            except Exception as e:
                print(f"[!] Ошибка с {file_path.name}: {e}")

    print("=" * 70)
    print(f"✅ ГОТОВО. Удалено файлов: {deleted_count}")
    print(f"   Освобождено места: {deleted_size / 1024 / 1024:.1f} МБ")
    print("=" * 70)


if __name__ == "__main__":
    main()