"""
Приблуда на python — читалка ленты котировок Quik (lua v3).
Читает data/quik_quotes.csv (lua переписывает атомарно раз в ~2 c).
Ничего не меняет, только чтение. Архитектура: connectors/quik/.
"""
import csv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
QUOTES_CSV = BASE_DIR / "data" / "quik_quotes.csv"


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


class QuotesReader:
    def __init__(self, path=None):
        self.path = Path(path) if path else QUOTES_CSV

    def read(self):
        """Возвращает {тикер: {...}}; файла нет или он рваный -> {}."""
        out = {}
        try:
            with open(self.path, "r", encoding="utf-8", errors="ignore") as f:
                for row in csv.DictReader(f, delimiter=";"):
                    t = (row.get("ticker") or "").strip()
                    if not t:
                        continue
                    out[t] = {
                        "class": (row.get("class") or "").strip(),
                        "last": _num(row.get("last")),
                        "bid": _num(row.get("bid")),
                        "offer": _num(row.get("offer")),
                        "voltoday": _num(row.get("voltoday")),
                        "valtoday": _num(row.get("valtoday")),
                        "numtrades": _num(row.get("numtrades")),
                        "openperiod": _num(row.get("openperiod")),
                        "tradingstatus": _num(row.get("tradingstatus")),
                        "ts": _num(row.get("ts")),
                    }
        except (FileNotFoundError, OSError):
            return {}
        return out