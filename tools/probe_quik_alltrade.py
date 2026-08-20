"""
Приблуда на python — диагностика моста QUIK -> Python.

Слушает UDP-порт, на который Lua-скрипт (quik/probe_alltrade.lua)
шлёт содержимое OnAllTrade. Просто печатает всё как есть — задача
этого шага увидеть РЕАЛЬНЫЕ названия полей (объём, цена, сторона,
время) прежде чем писать постоянную интеграцию с детектором.

Запуск: python tools/probe_quik_alltrade.py
"""

import asyncio

HOST = "127.0.0.1"
PORT = 3587


class Protocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.count = 0

    def datagram_received(self, data: bytes, addr) -> None:
        self.count += 1
        text = data.decode("utf-8", errors="replace").strip()
        print(f"[{self.count}] {text}")


async def main() -> None:
    loop = asyncio.get_running_loop()
    print(f"Слушаю UDP {HOST}:{PORT} — жду сделки от QUIK...")
    print("Открой Quik, запусти Lua-скрипт (Сервисы -> Lua скрипты -> Добавить -> Запустить),")
    print("и дождись хотя бы одной реальной обезличенной сделки по любому тикеру.\n")

    transport, protocol = await loop.create_datagram_endpoint(
        Protocol, local_addr=(HOST, PORT)
    )
    try:
        await asyncio.sleep(3600)
    finally:
        transport.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass