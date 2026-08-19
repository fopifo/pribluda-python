"""
Приблуда на python — очистка ненужных файлов и папок проекта.
По умолчанию СУХОЙ ПРОГОН: печатает, что будет удалено и сколько места
освободится. Реально удаляет только с флагом --do.
Чистит:
  output/  — отчёты (snapshot_full_*, compare_*, gt_*, loose_* и пр.)
             старше keep_output дней (по умолчанию 1 — сегодня оставляем);
  data/    — ленты {TICKER}_{дата}.json старше keep_data дней (по умолч. 2);
  **/__pycache__ — кэш Python.
Запуск:
  python tools/cleanup.py            # посмотреть, что удалится
  python tools/cleanup.py --do       # реально удалить
  python tools/cleanup.py --do --keep-data 7   # держать ленты 7 дней
"""
import os, sys, shutil, time
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def _age_days(p):
    return (time.time() - p.stat().st_mtime) / 86400.0


def _human(n):
    for u in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024.0
    return f"{n:.1f} ТБ"


def main():
    do = "--do" in sys.argv
    keep_data = 2
    keep_output = 1
    if "--keep-data" in sys.argv:
        keep_data = int(sys.argv[sys.argv.index("--keep-data") + 1])
    if "--keep-output" in sys.argv:
        keep_output = int(sys.argv[sys.argv.index("--keep-output") + 1])

    to_delete = []  # (path, is_dir)

    out = BASE / "output"
    if out.exists():
        for p in out.iterdir():
            if p.is_file() and _age_days(p) > keep_output:
                to_delete.append((p, False))

    data = BASE / "data"
    if data.exists():
        cutoff = (datetime.now() - timedelta(days=keep_data)).date()
        for p in data.iterdir():
            if not p.is_file():
                continue
            # дата из имени {TICKER}_{YYYY-MM-DD}.json
            stem = p.stem
            date_part = stem.rsplit("_", 1)[-1]
            try:
                fdate = datetime.strptime(date_part, "%Y-%m-%d").date()
                old = fdate < cutoff
            except ValueError:
                old = _age_days(p) > keep_data
            if old:
                to_delete.append((p, False))

    for root, dirs, _files in os.walk(BASE):
        for d in dirs:
            if d == "__pycache__":
                to_delete.append((Path(root) / d, True))

    total = 0
    print(f"Режим: {'УДАЛЕНИЕ' if do else 'ПРОСМОТР (добавь --do для удаления)'}")
    print(f"Порог: output>{keep_output}д, data>{keep_data}д")
    for p, is_dir in to_delete:
        if is_dir:
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        else:
            size = p.stat().st_size
        total += size
        print(f"  {'[DIR ]' if is_dir else '[FILE]'} {p.relative_to(BASE)}  ({_human(size)})")
    print(f"Итого к удалению: {len(to_delete)} объектов, {_human(total)}")

    if do:
        for p, is_dir in to_delete:
            try:
                if is_dir:
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink()
            except OSError as e:
                print(f"  не удалось удалить {p}: {e}")
        print("Готово.")


if __name__ == "__main__":
    main()