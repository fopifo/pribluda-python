"""
Приблуда на python — разовый "зонд" большого файла истории сделок.
Не парсит файл в память чата — печатает КОМПАКТНУЮ сводку (структура,
разделители, примеры строк), которую можно целиком скопировать помощнику.
Философия та же, что в weekly_review.py: тяжёлое — локально, помощнику —
компактный отчёт.
Запуск (из корня проекта):
python analysis/probe_week_trades.py [путь_к_файлу]
По умолчанию смотрит data_sample/Week_trades.txt.
"""
import sys
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PATH = BASE_DIR / "data_sample" / "Week_trades.txt"
SAMPLE_HEAD = 10
SAMPLE_TAIL = 3
DELIMITERS = ["\t", ";", ",", "|"]


def guess_delimiter(line: str) -> str:
    counts = {d: line.count(d) for d in DELIMITERS}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else "(пробелы/другое)"


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    if not path.exists():
        print(f"Файл не найден: {path}")
        return

    total = 0
    head = []
    tail = []
    first_tokens = Counter()
    delim_counter = Counter()
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            total += 1
            if len(head) < SAMPLE_HEAD:
                head.append(line)
            tail.append(line)
            if len(tail) > SAMPLE_TAIL:
                tail.pop(0)
            first_tokens[line.split()[0]] += 1
            delim_counter[guess_delimiter(line)] += 1

    print(f"Файл: {path}")
    print(f"Всего непустых строк: {total}")
    print(f"\nРазделители (по всем строкам): {dict(delim_counter)}")
    print(f"\nПервые {len(head)} строк как есть:")
    for i, line in enumerate(head, 1):
        print(f"  {i:3d}| {line}")
    print(f"\nПоследние {len(tail)} строк:")
    for line in tail:
        print(f"      | {line}")
    print(f"\nТоп-10 первых полей (возможно, тикеры):")
    for token, cnt in first_tokens.most_common(10):
        print(f"  {token:<12} {cnt}")


if __name__ == "__main__":
    main()