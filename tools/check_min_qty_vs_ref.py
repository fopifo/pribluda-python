"""
Приблуда на python — сверка min_qty в ticker_settings.json с реальными
объёмами роботов конкурента (data/competitor_history.jsonl).

Идея: CNRU показал класс ошибки — min_qty стоит на 1-2 лота выше
реального объёма робота, детектор фильтрует его сделки на входе, и
робот физически не может накопить ни одного повтора. Этот скрипт ищет
все такие случаи разом по всему эталону, а не по одному тикеру вручную.

НИЧЕГО НЕ МЕНЯЕТ — только печатает список подозрительных тикеров и
рекомендуемое новое значение (наблюдаемый минимум минус небольшой
запас). Применять правки в ticker_settings.json — отдельным шагом,
после ручной проверки списка (см. итог: печатается предупреждение
"сначала проверь глазами 3-5 строк").

Использование (из корня проекта):
    python tools/check_min_qty_vs_ref.py
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from core.ticker_settings import load_settings

COMP_PATH = BASE / "data" / "competitor_history.jsonl"
MARGIN = 2  # насколько ниже наблюдаемого минимума ставить новый порог


def load_ref():
    refs = []
    if not COMP_PATH.exists():
        print(f"Файл эталона не найден: {COMP_PATH}")
        return refs
    with open(COMP_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if isinstance(rec, dict) and "symbol" in rec and "qty_variants" in rec:
                    refs.append(rec)
            except Exception:
                continue
    return refs


def main() -> None:
    settings = load_settings()
    refs = load_ref()
    print(f"Эталон: {len(refs)} записей")
    print(f"Тикеров в ticker_settings.json: {len(settings)}")
    print()

    # Минимальный наблюдаемый qty конкурента по каждому тикеру
    min_qty_by_symbol: dict[str, int] = defaultdict(lambda: 10**9)
    examples_by_symbol: dict[str, list] = defaultdict(list)
    for r in refs:
        sym = r.get("symbol")
        qty_variants = r.get("qty_variants") or []
        if not sym or not qty_variants:
            continue
        obs_min = min(qty_variants)
        if obs_min < min_qty_by_symbol[sym]:
            min_qty_by_symbol[sym] = obs_min
        examples_by_symbol[sym].append(
            f"qty={qty_variants} int={r.get('interval_avg')} side={r.get('side')} ts={r.get('timestamp')}"
        )

    rows = []
    for sym, observed_min in min_qty_by_symbol.items():
        cur_cfg = settings.get(sym, {})
        cur_min_qty = cur_cfg.get("min_qty", 1)
        if cur_min_qty > observed_min:
            suggested = max(1, observed_min - MARGIN)
            rows.append((sym, cur_min_qty, observed_min, suggested))

    rows.sort(key=lambda r: -(r[1] - r[2]))  # по убыванию разрыва

    print(f"Найдено тикеров, где min_qty ВЫШЕ реального объёма конкурента: {len(rows)}")
    print()
    print(f"{'ТИКЕР':<8} {'текущий min_qty':>16} {'мин.у конкурента':>18} {'предложение':>12}")
    print("-" * 60)
    for sym, cur, observed, suggested in rows:
        print(f"{sym:<8} {cur:>16} {observed:>18} {suggested:>12}")

    print()
    print("=" * 60)
    print("ВАЖНО: ничего не изменено. Прежде чем применять массово —")
    print("проверь глазами 3-5 строк из списка выше по скринам эталона")
    print("(как было сделано для CNRU), чтобы убедиться, что это не")
    print("случайность/выброс, а устойчивая закономерность.")
    print("=" * 60)
    print()
    print("Примеры записей эталона по топ-5 тикерам разрыва:")
    for sym, cur, observed, suggested in rows[:5]:
        print(f"\n{sym}:")
        for ex in examples_by_symbol[sym][:5]:
            print(f"  {ex}")


if __name__ == "__main__":
    main()