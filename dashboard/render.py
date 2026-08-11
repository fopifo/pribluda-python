"""
Отрисовка живой таблицы активных роботов в консоли — аналог таблицы из
стороннего скринера (TICKER / SIDE / QTY / INT / LEN / NEXT).

Каждый вызов render_table() очищает экран и печатает текущий срез
целиком заново — старый вывод не накапливается, консоль не растёт со
временем, даже если скринер работает много часов подряд.
"""

import os
from datetime import datetime

COLUMN_WIDTHS = {
    "symbol": 8,
    "side": 6,
    "qty": 14,
    "interval": 8,
    "repeats": 5,
    "next": 16,
}


def _clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _format_next(seconds_to_next: float | None) -> str:
    if seconds_to_next is None:
        return "-"
    if seconds_to_next >= 0:
        return f"через {seconds_to_next:.0f}с"
    return f"ПРОСРОЧЕН {abs(seconds_to_next):.0f}с"


def _format_row(row: dict) -> str:
    symbol = row["symbol"]
    side = "BUY" if row["side"] == "buy" else "SELL"
    qty_str = "-".join(str(q) for q in row["qty_variants"])
    interval = row["interval"]
    interval_str = f"{interval:.1f}с" if interval is not None else "-"
    repeats = row["repeats"]
    next_str = _format_next(row["seconds_to_next"])
    preset = row.get("preset", "")

    return (
        f"{symbol:<{COLUMN_WIDTHS['symbol']}}"
        f"{side:<{COLUMN_WIDTHS['side']}}"
        f"{qty_str:<{COLUMN_WIDTHS['qty']}}"
        f"{interval_str:<{COLUMN_WIDTHS['interval']}}"
        f"{repeats:<{COLUMN_WIDTHS['repeats']}}"
        f"{next_str:<{COLUMN_WIDTHS['next']}}"
        f"{preset}"
    )


def _sort_key(row: dict):
    # сначала те, у кого следующий удар ближе всего (или уже просрочен)
    seconds = row["seconds_to_next"]
    return seconds if seconds is not None else float("inf")


def render_table(rows: list[dict]) -> None:
    _clear_screen()

    header = (
        f"{'TICKER':<{COLUMN_WIDTHS['symbol']}}"
        f"{'SIDE':<{COLUMN_WIDTHS['side']}}"
        f"{'QTY':<{COLUMN_WIDTHS['qty']}}"
        f"{'INT':<{COLUMN_WIDTHS['interval']}}"
        f"{'LEN':<{COLUMN_WIDTHS['repeats']}}"
        f"{'NEXT':<{COLUMN_WIDTHS['next']}}"
        f"PRESET"
    )
    print(header)
    print("-" * len(header))

    for row in sorted(rows, key=_sort_key):
        print(_format_row(row))

    print()
    print(f"Обновлено: {datetime.now().strftime('%H:%M:%S')}  |  активных серий: {len(rows)}")
    print("(полный лог событий пишется в файл output/live_signals_<дата>.txt)")