"""
ai_solution_v1/dump.py
======================
Снимок всего проекта ai_solution_v1 в один текстовый файл (Markdown).

Запуск из корня dpNEW:
    python -m ai_solution_v1.dump
    python -m ai_solution_v1.dump --out dump_output.md
    python -m ai_solution_v1.dump --include-pycache

Что делает:
  1) рекурсивно обходит папку ai_solution_v1 (включая саму эту утилиту);
  2) пропускает __pycache__, .venv, venv, .git, .vscode, .idea,
     *.pyc/*.pyo, сам дамп-файл и временные файлы (~, .tmp, .bak);
  3) пишет Markdown: оглавление с размерами + по каждому файлу
     заголовок "### Файл: <путь>" и содержимое в ``` -блоке.

Только чтение исходников и запись одного отчёта ВНУТРИ
ai_solution_v1. Нет сети, нет GUI, нет торговли.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

__all__ = ["build_dump", "main", "iter_project_files", "DEFAULT_OUT"]

BASE_DIR = Path(__file__).resolve().parent          # .../dpNEW/ai_solution_v1
PROJECT_NAME = BASE_DIR.name
DEFAULT_OUT = "dump_output.md"

# Папки, которые НЕ попадают в дамп (сравнение по имени, любой уровень).
EXCLUDE_DIRS = frozenset({
    "__pycache__",
    ".venv",
    "venv",
    ".env",
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
})

# Файлы, которые НЕ попадают в дамп.
EXCLUDE_SUFFIXES = frozenset({
    ".pyc", ".pyo", ".pyd", ".so", ".dll",
    ".exe", ".zip", ".7z", ".rar", ".gz", ".tar",
    ".log", ".tmp", ".bak", ".swp", ".lock",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
    ".parquet", ".feather", ".pkl", ".pickle", ".npy", ".npz",
    ".db", ".sqlite", ".sqlite3",
})

EXCLUDE_NAMES = frozenset({
    DEFAULT_OUT,
    "dump_output.txt",
    "Thumbs.db",
    ".DS_Store",
    "desktop.ini",
})

# Как читать файлы с кодом; всё прочее — как текст с заменой ошибок.
_TEXT_ENCODING = "utf-8"
_MAX_FILE_BYTES = 8 * 1024 * 1024    # защита: не засасываем гигантские файлы


# ---------------------------------------------------------------------------
# Обход
# ---------------------------------------------------------------------------
def _is_excluded_dir(name: str) -> bool:
    return name in EXCLUDE_DIRS or name.startswith(".")


def iter_project_files(
    base: Path = BASE_DIR,
    out_paths: Sequence[Path] = (),
) -> List[Path]:
    """
    Отсортированный список файлов проекта (относительно base), кроме
    исключённых. out_paths — полные пути самого дампа (исключаются).
    """
    excluded_abs = {p.resolve() for p in out_paths}
    found: List[Path] = []
    for root, dirs, files in os.walk(base):
        root_path = Path(root)
        # Прунинг папок на месте: os.walk не заходит в исключённые.
        dirs[:] = sorted(d for d in dirs if not _is_excluded_dir(d))
        for fname in sorted(files):
            if fname in EXCLUDE_NAMES:
                continue
            if Path(fname).suffix.lower() in EXCLUDE_SUFFIXES:
                continue
            full = root_path / fname
            try:
                if full.is_symlink() or not full.is_file():
                    continue
                if full.resolve() in excluded_abs:
                    continue
            except OSError:
                continue
            found.append(full)
    # Стабильный порядок: сначала мелкая глубина, затем по алфавиту пути.
    found.sort(key=lambda p: (p.relative_to(base).as_posix().count("/"),
                              p.relative_to(base).as_posix().lower()))
    return found


# ---------------------------------------------------------------------------
# Чтение содержимого
# ---------------------------------------------------------------------------
def _read_text(path: Path) -> Tuple[str, str]:
    """
    Прочитать файл как текст. Возвращает (содержимое, язык_для_блока).
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        return f"<<ошибка чтения: {exc}>>", "text"
    if size > _MAX_FILE_BYTES:
        return f"<<файл пропущен: {size} байт > {_MAX_FILE_BYTES}>>", "text"
    ext = path.suffix.lower()
    lang = {
        ".py": "python",
        ".md": "markdown",
        ".json": "json",
        ".jsonl": "json",
        ".toml": "toml",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".txt": "text",
        ".bat": "bat",
        ".ps1": "powershell",
        ".sh": "bash",
        ".cfg": "ini",
        ".ini": "ini",
    }.get(ext, "text")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return f"<<ошибка чтения: {exc}>>", "text"
    # BOM снимаем, иначе он ляжет в дамп невидимым символом.
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        text = raw.decode(_TEXT_ENCODING)
    except UnicodeDecodeError:
        text = raw.decode(_TEXT_ENCODING, errors="replace")
        text = "<<ВНИМАНИЕ: файл не в UTF-8, часть символов заменена>>\n" + text
    return text, lang


def _human(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} МБ"
    if size >= 1024:
        return f"{size / 1024:.1f} КБ"
    return f"{size} Б"


# ---------------------------------------------------------------------------
# Сборка дампа
# ---------------------------------------------------------------------------
def build_dump(
    base: Path = BASE_DIR,
    out: Path = BASE_DIR / DEFAULT_OUT,
    include_pycache: bool = False,
) -> Tuple[Path, int, int]:
    """
    Собрать дамп. Возвращает (путь, число файлов, суммарный размер байт).
    Пишем через временный файл и подмену — чтобы прерванный дамп
    не оставил полусырой файл.
    """
    out = out.resolve()
    files = iter_project_files(base, out_paths=[out])
    # Внутреннее оглавление: относительный путь -> размер.
    entries: List[Tuple[Path, int]] = []
    total_bytes = 0
    for f in files:
        try:
            size = f.stat().st_size
        except OSError:
            size = 0
        entries.append((f, size))
        total_bytes += size

    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    py_files = sum(1 for f, _ in entries if f.suffix.lower() == ".py")

    lines: List[str] = []
    add = lines.append

    add(f"# Дамп проекта {PROJECT_NAME}")
    add("")
    add(f"- Сформирован: `{stamp}`")
    add(f"- Корень: `{base}`")
    add(f"- Файлов: **{len(entries)}** (из них .py: {py_files})")
    add(f"- Суммарный размер исходников: {_human(total_bytes)}")
    add("")
    add("## Оглавление")
    add("")
    for f, size in entries:
        rel = f.relative_to(base).as_posix()
        add(f"- `{rel}` — {_human(size)}")
    add("")
    add("---")
    add("")

    for idx, (f, size) in enumerate(entries, start=1):
        rel = f.relative_to(base).as_posix()
        text, lang = _read_text(f)
        add(f"### Файл: {rel}")
        add("")
        add(f"> {idx}/{len(entries)} · {_human(size)}")
        add("")
        # Длина забора подбирается по максимальной серии бэктиков в тексте,
        # чтобы содержимое не «сломало» Markdown.
        fence = "```"
        run = 2
        for ln in text.splitlines():
            stripped = ln.lstrip()
            if stripped.startswith("```"):
                run = max(run, len(stripped) - len(stripped.lstrip("`")) + 1)
        fence = "`" * max(run, 3)
        add(f"{fence}{lang}")
        add(text.rstrip("\n") if text.strip() else "<пустой файл>")
        add(fence)
        add("")

    data = "\n".join(lines) + "\n"
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8", errors="replace")
    # На Windows os.replace не перезапишет существующий файл, если тот
    # открыт в редакторе, — дадим понятную ошибку вместо трейсбека.
    try:
        os.replace(tmp, out)
    except PermissionError as exc:
        raise PermissionError(
            f"не удалось перезаписать {out} — закройте дамп в редакторе "
            f"или укажите другой --out ({exc})"
        ) from exc
    return out, len(entries), total_bytes


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="ai_solution_v1.dump",
        description="Снимок проекта ai_solution_v1 в один Markdown-файл.",
    )
    p.add_argument(
        "--out", default=str(BASE_DIR / DEFAULT_OUT), metavar="PATH",
        help=f"куда писать дамп (по умолчанию {PROJECT_NAME}/{DEFAULT_OUT})",
    )
    p.add_argument(
        "--root", default=str(BASE_DIR), metavar="DIR",
        help="корень обхода (по умолчанию папка ai_solution_v1)",
    )
    p.add_argument(
        "--include-pycache", action="store_true",
        help="включить __pycache__ (по умолчанию исключён)",
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    base = Path(args.root).resolve()
    if not base.is_dir():
        print(f"Ошибка: нет папки {base}", file=sys.stderr)
        return 2
    out = Path(args.out).resolve()

    path, count, total = build_dump(base, out, include_pycache=args.include_pycache)
    print(f"[dump] готово: {count} файлов, {_human(total)} -> {path}")
    try:
        print(f"[dump] размер дампа: {_human(path.stat().st_size)}")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
