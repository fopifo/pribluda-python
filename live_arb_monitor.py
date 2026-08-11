"""
Приблуда на python — живой монитор арбитражных связок.

Отдельный от live_screener.py процесс — подключается к WebSocket Алора
и следит только за тикерами из arb_pairs.json. Сделан отдельно от
основного скринера намеренно: логику расхождения нужно обкатать и
проверить изолированно, не трогая уже работающий live_screener.py.
Когда логика подтвердится на практике — можно будет объединить оба
потока в один процесс (одна WebSocket-подписка вместо двух), но это
отдельный шаг после проверки.

Нужна библиотека: pip install websockets --break-system-packages
"""

import asyncio
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import websockets
from dotenv import load_dotenv

from arb_config import load_pairs, get_pair_symbols
from arbitrage.pair_monitor import PairMonitor

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
OUTPUT_DIR = BASE_DIR / "output"

load_dotenv(ENV_PATH)

REFRESH_TOKEN = os.getenv("ALOR_REFRESH_TOKEN")
OAUTH_URL = "https://oauth.alor.ru/refresh"
WS_URL = "wss://api.alor.ru/ws"

BOARD = "TQBR"
EXCHANGE = "MOEX"
MOSCOW_TZ = ZoneInfo("Europe/Moscow")

RECONNECT_INTERVAL_SEC = 20 * 60


def get_access_token(refresh_token: str) -> str:
    response = requests.post(OAUTH_URL, params={"token": refresh_token})
    response.raise_for_status()
    return response.json()["AccessToken"]


def build_monitors() -> list[PairMonitor]:
    pairs = load_pairs()
    monitors = []
    for pair_name, cfg in pairs.items():
        monitors.append(PairMonitor(
            pair_name=pair_name,
            symbol_a=cfg["symbol_a"],
            symbol_b=cfg["symbol_b"],
            threshold_pct=cfg.get("threshold_pct", 1.5),
            half_life_sec=cfg.get("half_life_sec", 600.0),
        ))
    return monitors


def open_log_file():
    OUTPUT_DIR.mkdir(exist_ok=True)
    today = datetime.now(MOSCOW_TZ).date()
    log_path = OUTPUT_DIR / f"arb_signals_{today}.txt"
    return log_path, open(log_path, "a", encoding="utf-8")


def log_line(log_file, line: str) -> None:
    stamp = datetime.now(MOSCOW_TZ).strftime("%H:%M:%S")
    full_line = f"[{stamp}] {line}"
    print(full_line, flush=True)
    log_file.write(full_line + "\n")
    log_file.flush()


async def run_session(monitors: list[PairMonitor], symbols: set[str], log_file) -> None:
    access_token = get_access_token(REFRESH_TOKEN)
    print("Токен получен, подключаюсь к WebSocket (арбитраж)...")

    async with websockets.connect(WS_URL, ping_interval=15, ping_timeout=10) as ws:
        guid_to_symbol: dict[str, str] = {}

        for symbol in symbols:
            guid = str(uuid.uuid4())
            guid_to_symbol[guid] = symbol
            subscribe_msg = {
                "opcode": "AllTradesGetAndSubscribe",
                "exchange": EXCHANGE,
                "code": symbol,
                "instrumentGroup": BOARD,
                "depth": 0,
                "includeVirtualTrades": False,
                "format": "Simple",
                "guid": guid,
                "token": access_token,
            }
            await ws.send(json.dumps(subscribe_msg))

        print(f"Подписался на {len(symbols)} тикеров (связки), жду сделки...")

        loop = asyncio.get_event_loop()
        deadline = loop.time() + RECONNECT_INTERVAL_SEC

        while loop.time() < deadline:
            timeout = deadline - loop.time()
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(timeout, 0.1))
            except asyncio.TimeoutError:
                break

            message = json.loads(raw)

            if message.get("httpCode") is not None:
                if message.get("httpCode") != 200:
                    print(f"Ошибка подписки: {message}")
                continue

            guid = message.get("guid")
            symbol = guid_to_symbol.get(guid)
            if symbol is None:
                continue

            data = message.get("data")
            if not data:
                continue

            price = data["price"]
            ts = data["timestamp"] / 1000.0

            for monitor in monitors:
                signal = monitor.on_trade(symbol, price, ts)
                if signal is not None:
                    log_line(log_file, f"⚡ РАСХОЖДЕНИЕ  {signal}")


async def main() -> None:
    if not REFRESH_TOKEN:
        print("Не найден ALOR_REFRESH_TOKEN в .env")
        return

    monitors = build_monitors()
    if not monitors:
        print("В arb_pairs.json нет ни одной связки.")
        return

    symbols = get_pair_symbols(load_pairs())
    log_path, log_file = open_log_file()
    print(f"Лог пишется в {log_path}")
    for m in monitors:
        print(f"Связка {m.pair_name}: {m.symbol_a}/{m.symbol_b}, "
              f"порог {m.threshold_pct}%, half-life {m.half_life_sec}с")

    try:
        while True:
            try:
                await run_session(monitors, symbols, log_file)
            except websockets.exceptions.ConnectionClosed as e:
                print(f"Соединение разорвано ({e}), переподключаюсь через 5 сек...")
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"Ошибка сессии: {e}, переподключаюсь через 5 сек...")
                await asyncio.sleep(5)
            print("Переподключение (плановое обновление токена)...")
    finally:
        log_file.close()
        print("\nОстановлено, лог сохранён.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass