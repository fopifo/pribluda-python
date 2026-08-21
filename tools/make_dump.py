"""
Приблуда на python — сборщик полного дампа исходников для передачи в чат.
v3: в дамп ДОБАВЛЕНЫ файлы статистики из data/ (whitelist):
    robots_history.jsonl, competitor_history.jsonl.
Обход дерева автоматически подхватывает новые папки.
Пропускает тяжёлое: data/* (кроме whitelist), output/, __pycache__, .git.
Пишет project_dump.txt в корень.
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "project_dump.txt"

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "venv", ".venv",
             "output", "node_modules", ".idea", ".vscode"}
OK_EXT = {".py", ".lua", ".md", ".txt", ".json", ".ini", ".csv"}
MAX_SIZE = 300_000
# whitelist из data/ — только статистика, НЕ quik_trades.csv
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
        if "data" in parts:
            if p.name not in DATA_INCLUDE:
                continue
        if p.suffix not in OK_EXT:
            continue
        if p.name == "project_dump.txt":
            continue
        if p.stat().st_size > MAX_SIZE:
            continue
        files.append(p)
    return files


def main():
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
    OUT.write_text("\n".join(chunks), encoding="utf-8")
    print(f"Файлов в дампе: {total}")
    print(f"Размер: {OUT.stat().st_size/1024:.0f} KB")
    print(f"Сохранено: {OUT}")


if __name__ == "__main__":
    main()