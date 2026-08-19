"""Чтение данных о планках из CSV"""
import csv
from pathlib import Path
from typing import Dict, List
from dataclasses import dataclass

BASE_DIR = Path(__file__).resolve().parent.parent.parent
LIMITS_FILE = BASE_DIR / "data" / "quik_limits.csv"


@dataclass
class LimitData:
    ticker: str
    current_price: float
    limit_up: float
    limit_down: float
    change_percent: float
    distance_to_up: float
    distance_to_down: float


class LimitsReader:
    def __init__(self):
        self.limits: Dict[str, LimitData] = {}
    
    def read_limits(self) -> Dict[str, LimitData]:
        if not LIMITS_FILE.exists():
            return {}
        
        try:
            with open(LIMITS_FILE, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f, delimiter=";")
                limits = {}
                
                for row in reader:
                    ticker = row["ticker"].strip()
                    current = float(row["current_price"])
                    up = float(row["limit_up"])
                    down = float(row["limit_down"])
                    change = float(row["change_percent"])
                    
                    # Расчёт расстояния до планок
                    dist_up = ((up - current) / current * 100) if current > 0 else 100
                    dist_down = ((current - down) / current * 100) if current > 0 else 100
                    
                    limits[ticker] = LimitData(
                        ticker=ticker,
                        current_price=current,
                        limit_up=up,
                        limit_down=down,
                        change_percent=change,
                        distance_to_up=dist_up,
                        distance_to_down=dist_down
                    )
                
                self.limits = limits
                return limits
                
        except Exception as e:
            print(f"Error reading limits: {e}")
            return {}
    
    def get_near_limits(self, max_percent: float = 5.0) -> List[LimitData]:
        """Получить тикеры в пределах max_percent% от планки"""
        self.read_limits()
        
        result = []
        for limit in self.limits.values():
            if limit.distance_to_up <= max_percent or limit.distance_to_down <= max_percent:
                result.append(limit)
        
        # Сортировка по близости
        result.sort(key=lambda x: min(x.distance_to_up, x.distance_to_down))
        return result