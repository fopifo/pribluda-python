"""
Приблуда на python — просмотр сырых сделок в заданном временном окне.
Нужен, чтобы глазами проверить найденный детектором сигнал.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_FILE = BASE_DIR / "data" / "SBER_2026-07-31.json"

# Окно на 30 секунд шире с каждой стороны от найденного сигнала,
# чтобы видеть контекст (что было до и после серии).
WINDOW_START = "2026-07-31T11:23:35"
WINDOW_END = "2026-07-31T11:24:46"


def main() -> None:
    with open(DATA_FILE, encoding="utf-8") as f:
        trades = json.load(f)

    start = datetime.fromisoformat(WINDOW_START).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(WINDOW_END).replace(tzinfo=timezone.utc)

    print(f"Сделки в окне {WINDOW_START} — {WINDOW_END}:\n")
    for t in trades:
        trade_time = datetime.fromisoformat(t["time"].replace("Z", "+00:00"))
        if start <= trade_time <= end:
            print(
                f"  {trade_time:%H:%M:%S.%f}  {t['side']:>4}  "
                f"qty={t['qty']:>6}  price={t['price']}"
            )


if __name__ == "__main__":
    main()