"""
WebSocket-клиент Алор API для стриминга ленты сделок.
Заменяет export_trades.lua + CSV reader.
"""
import asyncio
import json
import websockets
from pathlib import Path

ALOR_WS_URL = "wss://api.alor.ru/ws"

class AlorStreamClient:
    def __init__(self, token, tickers, on_trade_callback):
        self.token = token
        self.tickers = tickers  # список ["SBER", "GAZP", ...]
        self.on_trade = on_trade_callback
        self.ws = None
    
    async def connect(self):
        """Подключается к WebSocket, подписывается на все тикеры."""
        async with websockets.connect(ALOR_WS_URL) as ws:
            self.ws = ws
            # Подписка на каждый тикер
            for ticker in self.tickers:
                msg = {
                    "opcode": "AllTradesGetAndSubscribe",
                    "exchange": "MOEX",
                    "code": ticker,
                    "instrumentGroup": "TQBR",
                    "token": self.token,
                    "format": "Simple"
                }
                await ws.send(json.dumps(msg))
            
            # Читаем поток сделок
            async for message in ws:
                data = json.loads(message)
                if "data" in data:
                    trade = data["data"]
                    # Конвертируем в формат, совместимый с backend.py
                    trade_dict = {
                        "symbol": trade["symbol"],
                        "qty": trade["qty"],
                        "price": trade["price"],
                        "side": trade["side"],  # НАСТОЯЩАЯ сторона!
                        "timestamp": trade["timestamp"],  # Unix ms
                        "board": trade["board"],
                        "id": trade["id"]
                    }
                    await self.on_trade(trade_dict)
    
    async def run(self):
        """Запускает стрим с реконнектом при разрыве."""
        while True:
            try:
                await self.connect()
            except Exception as e:
                print(f"[AlorStream] разрыв: {e}, реконнект через 5с")
                await asyncio.sleep(5)