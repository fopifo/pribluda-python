"""
Приблуда на python — разрезает большие текстовые файлы (логи) на части,
пригодные для пересылки в чат.

Использование:
  python split_logs.py [файл1 файл2 ...]

Если файлы не указаны, скрипт находит все live_signals_*.txt в папке
output/ и режет их все. Для каждого файла создаётся своя подпапка в
parts/ (имя = имя файла без расширения), внутри которой лежат части
part_001.txt, part_002.txt, ... размером не более MAX_CHARS символов.
"""

import sys
from pathlib import Path

MAX_CHARS = 3800  # безопасный размер для одного сообщения

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
PARTS_DIR = BASE_DIR / "parts"


def split_one_file(file_path: Path) -> None:
    """Режет один файл на части и кладёт их в parts/<stem>/"""
    if not file_path.exists():
        print(f"Файл {file_path} не найден, пропускаю.")
        return

    text = file_path.read_text(encoding="utf-8")
    if not text:
        print(f"Файл {file_path} пустой, пропускаю.")
        return

    # Создаём подпапку для этого файла
    file_parts_dir = PARTS_DIR / file_path.stem
    file_parts_dir.mkdir(parents=True, exist_ok=True)

    parts = []
    start = 0
    total = len(text)

    while start < total:
        end = min(start + MAX_CHARS, total)
        # Не рвём строку пополам (если кусок не последний)
        if end < total:
            newline_pos = text.rfind("\n", start, end)
            if newline_pos > start:
                end = newline_pos + 1
        parts.append(text[start:end])
        start = end

    for idx, part in enumerate(parts, 1):
        part_file = file_parts_dir / f"part_{idx:03d}.txt"
        part_file.write_text(part, encoding="utf-8")
        print(f"Сохранено: {part_file} ({len(part)} символов)")

    print(f"Файл {file_path.name} разрезан на {len(parts)} частей.\n")


def main() -> None:
    # Если переданы аргументы — берём их как пути к файлам
    if len(sys.argv) > 1:
        files = [Path(arg) for arg in sys.argv[1:]]
    else:
        # Иначе ищем все live_signals_*.txt в output/
        if not OUTPUT_DIR.exists():
            print(f"Папка {OUTPUT_DIR} не найдена. Укажите файлы явно.")
            return
        files = sorted(OUTPUT_DIR.glob("live_signals_*.txt"))
        if not files:
            print("В папке output/ нет файлов live_signals_*.txt")
            return

    PARTS_DIR.mkdir(exist_ok=True)

    for file_path in files:
        split_one_file(file_path)


if __name__ == "__main__":
    main()