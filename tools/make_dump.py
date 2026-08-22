"""
Приблуда на python — сборщик полного дампа исходников для передачи в чат.
v4: в дамп ВСЕГДА попадают файлы статистики/эталона из data/:
robots_history.jsonl (наш поток, отладка) и
competitor_history.jsonl (эталон конкурента).
v5: файлы из whitelist data/ идут в дамп БЕЗ ограничения MAX_SIZE
(robots_history.jsonl бывает >300KB и раньше вырезался — это был баг).
Обход дерева подхватывает новые папки автоматически.
Пропускает тяжёлое: data/* (кроме whitelist), output/, __pycache__, .git.
Пишет project_dump_YYYY-MM-DD_HH-MM-SS.txt в корень.
"""
import sys
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent.parent

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "venv", ".venv",
             "output", "node_modules", ".idea", ".vscode"}
OK_EXT = {".py", ".lua", ".md", ".txt", ".json", ".ini", ".csv"}
MAX_SIZE = 300_000
# whitelist из data/ — статистика и эталон, НЕ quik_trades.csv
DATA_INCLUDE = {"robots_history.jsonl", "competitor_history.jsonl"}

def collect() -> list[Path]:
    files = []
    for p in sorted(BASE.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(BASE)
        parts = rel.parts
        if any(part in SKIP_DIRS for part in parts):
            continue
        in_data = "data" in parts
        if in_data and p.name not in DATA_INCLUDE:
            continue
        if p.suffix not in OK_EXT:
            continue
        # пропускаем все предыдущие дампы
        if p.name.startswith("project_dump_") and p.name.endswith(".txt"):
            continue
        # v5: лимит размера НЕ применяется к файлам статистики/эталона
        if not in_data and p.stat().st_size > MAX_SIZE:
            continue
        files.append(p)
    return files

def main():
    # имя с датой и временем
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = BASE / f"project_dump_{timestamp}.txt"
    
    files = collect()
    chunks = []
    total = 0
    for p in files:
        rel = p.relative_to(BASE).as_posix()
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        chunks.append("=" * 70)
        chunks.append(f"##### {rel}")
        chunks.append("=" * 70)
        chunks.append(text.rstrip("\n"))
        chunks.append("")
        total += 1
    out_path.write_text("\n".join(chunks), encoding="utf-8")
    print(f"Файлов в дампе: {total}")
    print(f"Размер: {out_path.stat().st_size/1024:.0f} KB")
    print(f"Сохранено: {out_path}")

if __name__ == "__main__":
    main()