"""
Приблуда на python — фоновый поток приёма UDP из Quik.
Слушает 127.0.0.1:3587, разбирает строки сделок и подаёт их в детекторы.
Обновляет shared_state.rows для GUI.
Запускается через quik_backend.start_backend(shared_state).
"""

import socket
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config import get_detector_configs
from dashboard.collector import collect_rows
from detectors.interval_robot import IntervalRobotDetector
from ticker_settings import get_active_symbols, load_settings

UDP_IP = "127.0.0.1"
UDP_PORT = 3587
REFRESH_SEC = 1


def build_detectors_from_settings(settings: dict) -> dict[str, list[IntervalRobotDetector]]:
    """Создаёт детекторы для всех активных тикеров из ticker_settings.json."""
    detectors = {}
    for symbol in get_active_symbols(settings):
        override = settings.get(symbol, {})
        # Для Quik нет сохранённой истории, поэтому min_qty берём из ручной
        # настройки, если она есть; иначе 1 (минимальный фильтр).
        min_qty = override.get("min_qty", 1)
        configs = get_detector_configs(symbol, min_qty, override)
        detectors[symbol] = [IntervalRobotDetector(symbol, cfg) for cfg in configs]
        print(f"{symbol}: min_qty = {min_qty}")
    return detectors


def parse_trade_line(line: str) -> dict | None:
    """Разбирает строку от Quik в словарь сделки."""
    parts = line.strip().split(";")
    if len(parts) < 5:
        return None
    try:
        symbol = parts[0]
        qty = int(parts[1])
        price = float(parts[2])
        side = parts[3] if parts[3] in ("buy", "sell") else "buy"
        timestamp = int(parts[4])  # миллисекунды
    except (ValueError, IndexError):
        return None
    return {
        "symbol": symbol,
        "qty": qty,
        "price": price,
        "side": side,
        "timestamp": timestamp,
    }


def start_backend(shared_state):
    """Запускает UDP-приёмник и цикл обновления GUI."""
    settings = load_settings()
    detectors = build_detectors_from_settings(settings)
    print(f"Создано детекторов для {len(detectors)} тикеров")
    print(f"Слушаю UDP {UDP_IP}:{UDP_PORT} ...")

    # Создаём UDP-сокет
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    sock.settimeout(0.2)  # таймаут 200 мс, чтобы цикл не блокировался

    last_gui_update = 0.0

    while True:
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            data = None

        if data:
            text = data.decode("utf-8", errors="ignore")
            # Может быть несколько строк в одном пакете
            for line in text.splitlines():
                trade = parse_trade_line(line)
                if trade is None:
                    continue
                symbol = trade["symbol"]
                if symbol not in detectors:
                    # Пропускаем тикеры, которых нет в нашем списке
                    continue
                for detector in detectors[symbol]:
                    detector.on_trade(trade)

        # Обновляем строки для GUI не чаще REFRESH_SEC
        now = time.time()
        if now - last_gui_update >= REFRESH_SEC:
            now_ts = datetime.now().timestamp()
            shared_state.rows = collect_rows(detectors, now_ts)
            shared_state.status = f"Quik | {len(shared_state.rows)} активных серий"
            last_gui_update = now