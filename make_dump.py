"""
Приблуда на python — автоматический полный дамп проекта.
Рекурсивно обходит все файлы проекта (кроме исключённых папок/файлов),
собирает их содержимое и сохраняет в dumps/.
Больше не включает PROJECT_SUMMARY.md — только актуальный код.
"""

from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DUMP_DIR = BASE_DIR / "dumps"
MAX_DUMPS = 10

# ---------------------------------------------------------------------------
# Настройка исключений – папки/файлы, которые НЕ попадут в дамп.
# Редактируй при необходимости.
# ---------------------------------------------------------------------------
EXCLUDE_DIRS = {
    ".git",
    "__pycache__",
    "venv",
    ".venv",
    "env",
    "dumps",
    "data",
    "output",
    ".mypy_cache",
    ".pytest_cache",
    "node_modules",
}

EXCLUDE_FILES = {
    ".env",
    "*.pyc",
    ".DS_Store",
    "Thumbs.db",
    # Документация / самодамп
    "PROJECT_SUMMARY.md",
    "README.md",
    "make_dump.py",
    # Разовые отладочные/исследовательские скрипты
    "inspect_window.py",
    "list_tqbr_shares.py",
    "explore_alltrades_history.py",
}

TEXT_EXTENSIONS = {".py", ".json", ".md", ".txt", ".toml", ".cfg", ".ini", ".yml", ".yaml"}
# ---------------------------------------------------------------------------

def is_excluded_dir(path: Path) -> bool:
    return any(part in EXCLUDE_DIRS for part in path.parts)

def is_excluded_file(file: Path) -> bool:
    name = file.name
    if name in EXCLUDE_FILES:
        return True
    if file.suffix == ".pyc":
        return True
    if name.startswith("."):
        return True
    return False

def collect_project_files() -> list[Path]:
    result = []
    for path in BASE_DIR.rglob("*"):
        if path.is_dir():
            continue
        if is_excluded_dir(path):
            continue
        if is_excluded_file(path):
            continue
        if path.suffix not in TEXT_EXTENSIONS:
            continue
        result.append(path)
    return sorted(result, key=lambda p: str(p.relative_to(BASE_DIR)))

def read_file_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        return f"(Ошибка чтения файла: {e})"

def build_dump_text() -> str:
    now = datetime.now()
    parts = [
        "=" * 70,
        "ПРИБЛУДА НА PYTHON — ПОЛНЫЙ ДАМП ПРОЕКТА",
        f"Сформирован: {now:%Y-%m-%d %H:%M:%S}",
        "=" * 70,
        "",
        "--- ИСХОДНЫЙ КОД (ВСЕ ФАЙЛЫ ПРОЕКТА) ---",
    ]

    project_files = collect_project_files()
    if not project_files:
        parts.append("(не найдено ни одного файла с кодом)")
        return "\n".join(parts)

    for file_path in project_files:
        relative = file_path.relative_to(BASE_DIR)
        parts.append("")
        parts.append("-" * 70)
        parts.append(f"### {relative}")
        parts.append("-" * 70)
        parts.append(read_file_safe(file_path))

    return "\n".join(parts)

def rotate(pattern: str) -> None:
    files = sorted(DUMP_DIR.glob(pattern))
    excess = len(files) - MAX_DUMPS
    for old_file in files[: max(excess, 0)]:
        old_file.unlink()

def main() -> None:
    DUMP_DIR.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dump_path = DUMP_DIR / f"dump_{timestamp}.txt"
    dump_path.write_text(build_dump_text(), encoding="utf-8")

    rotate("dump_*.txt")

    print(f"Дамп сохранён: {dump_path}")
    print(f"Хранится дампов: {len(list(DUMP_DIR.glob('dump_*.txt')))} (максимум {MAX_DUMPS})")

if __name__ == "__main__":
    main()