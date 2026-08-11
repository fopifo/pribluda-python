"""
Приблуда на python — настройки по тикерам, редактируемые вручную через
GUI (окно "⚙ Настройки тикеров"), а не через код.

Единственный источник правды — файл ticker_settings.json рядом с этим
модулем. Если файла ещё нет (первый запуск), он создаётся автоматически
из старого захардкоженного списка тикеров (BOOTSTRAP_SYMBOLS). После
первого запуска BOOTSTRAP_SYMBOLS больше нигде не используется —
тикеры живут только в json.

Поля по каждому тикеру (кроме "active", все необязательные — None
значит "использовать поведение по умолчанию"):
  active       — bool, мониторится ли тикер сейчас.
  min_qty      — int | None. Ручной порог объёма в лотах. Если задан —
                 используется как есть, автоматический процентиль не
                 считается.
  min_interval — float | None. Ручная нижняя граница интервала (сек),
                 переопределяет min_interval из пресета для ЭТОГО
                 тикера.
  min_repeats  — int | None. Ручной порог повторов, переопределяет
                 min_repeats из пресета.

Резервная копия: перед КАЖДОЙ перезаписью текущее содержимое файла
копируется в ticker_settings.json.bak (одна копия, перезаписывается
каждый раз — это не история версий, а просто "последнее рабочее
состояние на всякий случай"). Если правка в таблице что-то испортит
(случайно удалённый тикер, сбитое число) — можно вручную скопировать
.bak поверх ticker_settings.json и перезапустить программу.

Сама запись — атомарная (временный файл + os.replace), чтобы падение
программы посреди записи не оставило json в битом состоянии.
"""

import json
import os
import shutil
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent / "ticker_settings.json"
BACKUP_PATH = SETTINGS_PATH.with_suffix(".json.bak")

# Список тикеров для самой первой генерации ticker_settings.json (когда
# файла ещё нет). Тот же список, что раньше был TRACKED_SYMBOLS в
# config.py — миграция один в один, ничего не потеряется.
BOOTSTRAP_SYMBOLS = [
    "SBER", "FLOT", "HEAD", "PIKK", "MAGN", "FEES", "ASTR", "CHMF", "PHOR",
    "RENI", "TGKA",
    "MDMG", "FIXR", "MTLR", "MTLRP", "YDEX", "NLMK", "RUAL", "BSPB", "PLZL",
    "CBOM", "NMTP", "SGZH", "GAZP", "UPRO", "ALRS", "BANE", "SIBN", "ROSN",
    "SNGS", "SNGSP", "POSI", "MOEX", "VTBR", "BANEP", "ELMT", "AFKS", "RNFT",
    "BELU", "CNRU", "DOMRF", "UGLD", "SVCB", "T", "GLRX", "RAGR", "LKOH",
    "LENT", "GMKN", "AFLT", "IRAO", "X5", "ETLN", "NVTK", "MBNK", "MTSS",
    "SFIN", "OZON", "SBERP", "TRNFP", "IVAT", "DATA", "MVID", "SMLT", "OGKB",
    "TATNP", "MGNT", "AKFB", "WUSH", "MSNG", "TATN", "VKCO", "TRMK", "RTKM",
    "RTKMP",
]

EMPTY_OVERRIDE = {
    "active": True,
    "min_qty": None,
    "min_interval": None,
    "min_repeats": None,
}


def _bootstrap() -> dict:
    settings = {symbol: dict(EMPTY_OVERRIDE) for symbol in BOOTSTRAP_SYMBOLS}
    save_settings(settings)
    return settings


def load_settings() -> dict:
    """Читает ticker_settings.json. Если файла ещё нет — создаёт его из
    BOOTSTRAP_SYMBOLS (миграция со старого TRACKED_SYMBOLS)."""
    if not SETTINGS_PATH.exists():
        return _bootstrap()

    with open(SETTINGS_PATH, encoding="utf-8") as f:
        raw = json.load(f)

    # На случай, если в файле не хватает каких-то полей (например, после
    # ручного редактирования json руками) — подстраховываемся дефолтами.
    settings = {}
    for symbol, override in raw.items():
        merged = dict(EMPTY_OVERRIDE)
        merged.update(override)
        settings[symbol] = merged
    return settings


def save_settings(settings: dict) -> None:
    """Перед перезаписью бэкапит текущий файл в .bak, потом атомарно
    (временный файл + os.replace) пишет новое содержимое."""
    if SETTINGS_PATH.exists():
        shutil.copyfile(SETTINGS_PATH, BACKUP_PATH)

    tmp_path = SETTINGS_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp_path, SETTINGS_PATH)


def get_active_symbols(settings: dict) -> list[str]:
    """Тикеры с active=True, отсортированные по алфавиту."""
    return sorted(symbol for symbol, override in settings.items() if override.get("active", True))


def add_ticker(settings: dict, symbol: str) -> dict:
    """Добавляет тикер с настройками по умолчанию, если его ещё нет.
    Мутирует settings на месте и возвращает его же."""
    symbol = symbol.strip().upper()
    if symbol and symbol not in settings:
        settings[symbol] = dict(EMPTY_OVERRIDE)
    return settings


def remove_ticker(settings: dict, symbol: str) -> dict:
    """Убирает тикер совсем (не путать с active=False — это временное
    отключение, а это — полное удаление из списка)."""
    settings.pop(symbol, None)
    return settings