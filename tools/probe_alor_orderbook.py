"""
Приблуда на python — разовый пробник:確認 формата СТАКАНА в WebSocket
Алора. Не гадаем на неподтверждённом API (та же дисциплина, что и в
probe_tbank_news.py / probe_price_limits.py) — сначала смотрим сырое
сообщение, потом встраиваем стакан в live_screener.py.
Подключается к тому же WS, что live_screener, подписывается на стакан
одного тикера (SBER) и печатает первые N сырых сообщений целиком.
Нужны библиотеки: pip install websockets requests python-dotenv
Запуск (из корня проекта):
python tools/probe_alor_orderbook.py
"""
import asyncio
import json
import os
import uuid
from pathlib import Path

import requests
import websockets
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
REFRESH_TOKEN = os.getenv("ALOR_REFRESH_TOKEN")

OAUTH_URL = "https://oauth.alor.ru/refresh"
WS_URL = "wss://api.alor.ru/ws"
EXCHANGE = "MOEX"
BOARD = "TQBR"
SYMBOL = "SBER"      # один тикер для пробника
MAX_MESSAGES = 5     # сколько сырых сообщений стакана напечатать


def get_access_token(refresh_token: str) -> str:
    response = requests.post(OAUTH_URL, params={"token": refresh_token})
    response.raise_for_status()
    return response.json()["AccessToken"]


async def main() -> None:
    if not REFRESH_TOKEN:
        print("Не найден ALOR_REFRESH_TOKEN в .env")
        return
    access_token = get_access_token(REFRESH_TOKEN)
    print("Токен получен, подключаюсь к WebSocket...")
    async with websockets.connect(WS_URL, ping_interval=15, ping_timeout=10) as ws:
        guid = str(uuid.uuid4())
        # Пробуем опкод подписки на стакан Алора. Если формат/опкод
        # отличается — в выводе будет ошибка с httpCode, по ней поймём
        # реальный формат (для этого и пробник).
        subscribe_msg = {
            "opcode": "OrderBookGetAndSubscribe",
            "exchange": EXCHANGE,
            "code": SYMBOL,
            "instrumentGroup": BOARD,
            "depth": 10,
            "format": "Simple",
            "guid": guid,
            "token": access_token,
        }
        await ws.send(json.dumps(subscribe_msg))
        print(f"Подписался на стакан {SYMBOL}, жду сырые сообщения...")
        printed = 0
        while printed < MAX_MESSAGES:
            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            message = json.loads(raw)
            if message.get("httpCode") is not None and message.get("httpCode") != 200:
                print(f"ОШИБКА ПОДПИСКИ: {json.dumps(message, ensure_ascii=False)}")
                return
            print("=" * 70)
            print(json.dumps(message, ensure_ascii=False, indent=2))
            printed += 1
        print("Пробник завершён, формат стакана виден выше.")


if __name__ == "__main__":
    asyncio.run(main())