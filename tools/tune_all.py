"""
Приблуда на python — автоматический min_qty по эталону конкурента ДЛЯ ВСЕХ тикеров.
Читает research/competitor_robots_*.csv, для каждого тикера из ticker_settings.json
берёт МИНИМАЛЬНЫЙ объём робота конкурента и ставит
min_qty = min(текущий, max(3, int(0.9 * min_qty_робота))).
Только ОПУСКАЕТ пороги, никогда не поднимает — шум на ликвидных не вырастет.
Тикеров нет в эталоне — не трогает. Идемпотентно, пишет резервную копию.
Запуск: python tools/tune_all.py
"""
import csv
import json
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SETTINGS = BASE / "ticker_settings.json"


def parse_qty(q):
    """'64-65' -> 64, '2500' -> 2500, мусор -> None."""
    q = (q or "").strip()
    try:
        if "-" in q:
            return min(int(x) for x in q.split("-"))
        return int(q)
    except ValueError:
        return None


def main():
    min_robot = {}
    for csv_path in sorted((BASE / "research").glob("competitor_robots_*.csv")):
        with open(csv_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                t = (row.get("ticker") or "").strip().upper()
                if not t:
                    continue
                q = parse_qty(row.get("qty"))
                if q is None:
                    continue
                if t not in min_robot or q < min_robot[t]:
                    min_robot[t] = q
    if not min_robot:
        print("Эталон research/competitor_robots_*.csv не найден или пуст.")
        return

    data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    shutil.copy2(SETTINGS, BASE / "ticker_settings.json.bak")
    print(f"Резервная копия: ticker_settings.json.bak")
    changed = []
    for t, q in sorted(min_robot.items()):
        if t not in data:
            continue
        old = data[t].get("min_qty", 20)
        new_q = min(old, max(3, int(q * 0.9)))
        if new_q != old:
            data[t]["min_qty"] = new_q
            changed.append(f"{t}: {old} -> {new_q}")
    SETTINGS.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Тикеров в эталоне: {len(min_robot)}; в настройках изменено: {len(changed)}")
    for c in changed:
        print("  " + c)


if __name__ == "__main__":
    main()