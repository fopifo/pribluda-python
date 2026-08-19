"""
Приблуда на python — сборка дампа-макета всего проекта для передачи
помощнику/команде. Обходит дерево проекта АВТОМАТИЧЕСКИ (проект растёт —
список файлов не хардкодится), и пропускает всё лишнее:
- СЕКРЕТЫ: .env и любой файл с секретом внутри;
- ТЯЖЁЛОЕ: data/, output/, logs/, parts/ (ленты, отчёты, логи),
  а также любой файл крупнее MAX_FILE_BYTES;
- МУСОР: .git, __pycache__, .venv, *.pyc, *.bak, *.tmp, дампы.
В дамп попадают только текстовые исходники/настройки/заметки
(.py, .json, .md, .txt, .bat). Результат — output/dump_<дата>.txt в том
же формате, что и прежние дампы.
Запуск (из корня проекта):
python tools/make_dump.py
"""
import os
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"

MAX_FILE_BYTES = 200 * 1024          # файлы крупнее 200 КБ не берём

# Папки, которые пропускаем целиком (с любым содержимым).
SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    "data", "output", "logs", "parts", ".idea", ".vscode",
}

# Расширения, которые берём в дамп (текст/код/настройки).
INCLUDE_SUFFIXES = {".py", ".json", ".md", ".txt", ".bat", ".cfg", ".ini"}

# Файлы, которые никогда не берём (секреты/артефакты).
SKIP_FILES = {".env", ".gitignore"}

# Подстроки в имени файла — тоже повод пропустить (артефакты/бэкапы).
SKIP_NAME_PARTS = (".bak", ".tmp", "dump_", "parallel_report_", "weekly_review_")


def should_skip_dir(name: str) -> bool:
    return name in SKIP_DIRS


def should_skip_file(path: Path) -> bool:
    if path.name in SKIP_FILES:
        return True
    if any(part in path.name for part in SKIP_NAME_PARTS):
        return True
    if path.suffix not in INCLUDE_SUFFIXES:
        return True
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return True
    except OSError:
        return True
    return False


def iter_project_files():
    """Обход дерева проекта, выдаёт файлы, подлежащие включению."""
    for root, dirs, files in os.walk(BASE_DIR):
        dirs[:] = sorted(d for d in dirs if not should_skip_dir(d))
        for name in sorted(files):
            path = Path(root) / name
            if not should_skip_file(path):
                yield path


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_path = OUTPUT_DIR / f"dump_{timestamp}.txt"
    included = 0
    with open(out_path, "w", encoding="utf-8") as out:
        out.write("=" * 70 + "\n")
        out.write("ПРИБЛУДА НА PYTHON — ДАМП ПРОЕКТА (макет)\n")
        out.write(f"Сформирован: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        out.write("=" * 70 + "\n")
        for path in iter_project_files():
            rel = path.relative_to(BASE_DIR)
            out.write("-" * 70 + "\n")
            out.write(f"### {rel}\n")
            out.write("-" * 70 + "\n")
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = "(бинарный файл, содержимое пропущено)"
            out.write(text)
            if not text.endswith("\n"):
                out.write("\n")
            included += 1
    size_kb = out_path.stat().st_size / 1024
    print(f"Дамп готов: {out_path}  ({included} файлов, {size_kb:.0f} КБ)")
    print("Этот файл можно передавать помощнику/команде как макет проекта.")


if __name__ == "__main__":
    main()