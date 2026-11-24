"""
Приблуда на python — читалка планок из data/quik_limits.csv (lua v3).
Планки ставит биржа: PRICEMIN/PRICEMAX из Quik. Ничего не меняет, только чтение.
Архитектура: connectors/quik/.
"""
import csv
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
LIMITS_CSV = BASE_DIR / "data" / "quik_limits.csv"


@dataclass
class LimitData:
    ticker: str
    current_price: float
    limit_up: float
    limit_down: float
    change_percent: float
    distance_to_up: float    # % до верхней планки
    distance_to_down: float  # % до нижней планки

    def nearest(self):
        """(направление, дистанция) до ближайшей планки."""
        if self.distance_to_up <= self.distance_to_down:
            return "up", self.distance_to_up
        return "down", self.distance_to_down

    def day_position(self):
        """0% = нижняя планка, 100% = верхняя."""
        span = self.limit_up - self.limit_down
        if span <= 0:
            return None
        pos = (self.current_price - self.limit_down) / span * 100.0
        return max(0.0, min(100.0, pos))


class LimitsReader:
    def __init__(self, path=None):
        self.path = Path(path) if path else LIMITS_CSV
        self.limits = {}

    def read(self):
        """Все строки файла -> {тикер: LimitData}. Рваный файл -> {}."""
        limits = {}
        try:
            with open(self.path, "r", encoding="utf-8", errors="ignore") as f:
                for row in csv.DictReader(f, delimiter=";"):
                    t = (row.get("ticker") or "").strip()
                    if not t:
                        continue
                    try:
                        cur = float(row["current_price"])
                        up = float(row["limit_up"])
                        down = float(row["limit_down"])
                        change = float(row["change_percent"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if cur <= 0 or up <= 0 or down <= 0:
                        continue
                    dist_up = (up - cur) / cur * 100.0
                    dist_down = (cur - down) / cur * 100.0
                    limits[t] = LimitData(ticker=t, current_price=cur,
                                          limit_up=up, limit_down=down,
                                          change_percent=change,
                                          distance_to_up=dist_up,
                                          distance_to_down=dist_down)
        except (FileNotFoundError, OSError):
            return {}
        self.limits = limits
        return limits

    def get_near_limits(self, pct=5.0):
        """Список LimitData, у которых ближайшая планка в пределах pct%."""
        if not self.limits:
            self.read()
        return [l for l in self.limits.values()
                if min(l.distance_to_up, l.distance_to_down) <= pct]