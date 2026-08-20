"""
Приблуда на python — настройки вкладки "Аукционы": группы тикеров
(эшелоны), замьюченные, флаги показа. Файл auction_settings.json в корне.
Паттерн тот же, что core/ticker_settings.py: загрузка/сохранение целиком.
Архитектура: core/.
"""
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = BASE_DIR / "auction_settings.json"

DEFAULT_BLUE_CHIPS = [
    "SBER", "GAZP", "LKOH", "ROSN", "GMKN", "VTBR", "TATN", "NVTK",
    "AFLT", "ALRS", "MGNT", "MOEX", "MTSS", "NLMK", "PLZL", "RTKM",
    "SIBN", "TRNFP", "CHMF", "YDEX", "OZON", "T",
]
DEFAULT_FIRST_ECHELON = [
    "AFKS", "FLOT", "FEES", "PIKK", "SVCB", "HEAD", "X5", "MAGN",
    "PHOR", "TATNP", "SBERP", "GAZP", "VTBP", "RUAL", "SGZH", "BANE",
]

GROUP_COLORS = ["#4fc3f7", "#7ee787", "#ffa657", "#d2a8ff",
                "#ff7b72", "#79c0ff", "#e3b341", "#56d364"]


def group_color(index):
    return GROUP_COLORS[index % len(GROUP_COLORS)]


def default_settings():
    return {
        "groups": [
            {"name": "Голубые фишки", "tickers": list(DEFAULT_BLUE_CHIPS)},
            {"name": "1-й эшелон", "tickers": list(DEFAULT_FIRST_ECHELON)},
            {"name": "2-й эшелон", "tickers": []},
            {"name": "3-й эшелон", "tickers": []},
        ],
        "muted": [],
        "show_ungrouped": True,
        "show_muted": False,
    }


def load_auction_settings():
    """Битый/отсутствующий файл -> дефолт. Лишние поля игнорируются."""
    try:
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default_settings()
    cfg = default_settings()
    if not isinstance(raw, dict):
        return cfg
    if isinstance(raw.get("groups"), list):
        groups = []
        for g in raw["groups"]:
            if isinstance(g, dict) and str(g.get("name", "")).strip():
                tk = [str(t).strip().upper()
                      for t in g.get("tickers", []) if str(t).strip()]
                groups.append({"name": str(g["name"]).strip(), "tickers": tk})
        if groups:
            cfg["groups"] = groups
    if isinstance(raw.get("muted"), list):
        cfg["muted"] = [str(t).strip().upper() for t in raw["muted"] if str(t).strip()]
    if isinstance(raw.get("show_ungrouped"), bool):
        cfg["show_ungrouped"] = raw["show_ungrouped"]
    if isinstance(raw.get("show_muted"), bool):
        cfg["show_muted"] = raw["show_muted"]
    return cfg


def save_auction_settings(cfg):
    SETTINGS_FILE.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )