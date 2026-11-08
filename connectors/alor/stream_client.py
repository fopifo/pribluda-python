"""
WebSocket-клиент Алор OpenAPI для стриминга ленты обезличенных сделок.
Заменяет связку QUIK + export_trades.lua + CSV reader.

Источник истины по формату:
  https://alor.dev/docs/api/websocket/data-subscriptions/AllTradesGetAndSubscribe

Преимущества перед QUIK:
  - настоящая сторона сделки (buy/sell), не tick-rule — решает корень TP=0 (08-2026)
  - Unix-миллисекунды из коробки, без dt.ms-костылей
  - depth=N даёт backfill последних N сделок при подключении (замена CSV backfill)
  - нет тяжёлого QUIK на ноутбуке
"""
import asyncio
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    import websockets
    from websockets.asyncio.client import connect as ws_connect
except ImportError:
    raise ImportError("Установите websockets: pip install websockets")

try:
    import requests
except ImportError:
    raise ImportError("Установите requests: pip install requests")

# URL-ы API
ALOR_WS_URL = "wss://api.alor.ru/ws"
ALOR_REFRESH_URL = "https://oauth.alor.ru/refresh"

# Безопасные таймауты (секунды)
TOKEN_REFRESH_MARGIN_SEC = 120       # обновляем access-токен за 2 мин до истечения
WS_RECONNECT_BASE_SEC = 2
WS_RECONNECT_MAX_SEC = 60
REQUEST_TIMEOUT_SEC = 10


class AlorTokenManager:
    """Получает и кэширует access-токен по refresh-токену."""
    def __init__(self, refresh_token: str):
        self._refresh_token = refresh_token
        self._access_token: str | None = None
        self._expires_at: float = 0
        self._lock = threading.Lock()

    def get_access_token(self) -> str:
        with self._lock:
            if self._access_token and time.time() < self._expires_at - TOKEN_REFRESH_MARGIN_SEC:
                return self._access_token
            self._refresh()
            return self._access_token

    def _refresh(self):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        body = {"token": self._refresh_token}
        try:
            r = requests.post(ALOR_REFRESH_URL, headers=headers, json=body, timeout=REQUEST_TIMEOUT_SEC)
            if r.status_code == 200:
                data = r.json()
                self._access_token = data.get("AccessToken") or data.get("access_token")
                if not self._access_token:
                    raise ValueError(f"Нет AccessToken в ответе refresh: {data}")
                self._expires_at = float(data.get("expires_at", time.time() + 30 * 60))
                return
            if r.status_code in (400, 401, 404):
                r2 = requests.post(ALOR_REFRESH_URL, headers={"Authorization": f"Bearer {self._refresh_token}"}, timeout=REQUEST_TIMEOUT_SEC)
                if r2.status_code == 200:
                    data = r2.json()
                    self._access_token = data.get("AccessToken") or data.get("access_token")
                    if not self._access_token:
                        raise ValueError(f"Нет AccessToken в ответе refresh (bearer flow): {data}")
                    self._expires_at = float(data.get("expires_at", time.time() + 30 * 60))
                    return
            raise ValueError(f"Refresh failed: HTTP {r.status_code}, body={r.text[:200]}")
        except Exception as e:
            self._access_token = None
            self._expires_at = 0
            raise


def load_env_value(key: str, env_path: str | Path | None = None) -> str:
    if env_path is None:
        env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    env_path = Path(env_path)
    if not env_path.exists():
        raise FileNotFoundError(f".env не найден: {env_path}")
    with open(env_path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip().strip('"\'')
    raise KeyError(f"Переменная {key} не найдена в {env_path}")


class AlorStreamClient:
    def __init__(
        self,
        refresh_token: str,
        tickers: list[str],
        exchange: str = "MOEX",
        board: str = "TQBR",
        depth: int = 50,
        format_: str = "Simple",
        on_trade=None,
        on_status=None,
    ):
        self._token_mgr = AlorTokenManager(refresh_token)
        self.tickers = list(tickers)
        self.exchange = exchange
        self.board = board
        self.depth = depth
        self.format_ = format_
        self.on_trade = on_trade
        self.on_status = on_status
        self._ws = None
        self._running = False
        self._subscribed: set[str] = set()

    async def _subscribe_one(self, ws, ticker: str):
        msg = {
            "opcode": "AllTradesGetAndSubscribe",
            "exchange": self.exchange,
            "code": ticker,
            "instrumentGroup": self.board,
            "depth": self.depth,
            "format": self.format_,
            "includeVirtualTrades": False,
            "guid": str(uuid.uuid4()),
            "token": self._token_mgr.get_access_token(),
        }
        await ws.send(json.dumps(msg))

    async def _dispatch_message(self, message: str):
        try:
            obj = json.loads(message)
        except json.JSONDecodeError:
            return
        if "data" in obj and isinstance(obj["data"], dict):
            trade = obj["data"]
            out = {
                "symbol": trade.get("symbol") or trade.get("sym"),
                "qty": trade.get("qty") or trade.get("q"),
                "price": trade.get("price") or trade.get("px"),
                "side": trade.get("side") or trade.get("s"),
                "timestamp": trade.get("timestamp") or trade.get("t"),
                "board": trade.get("board") or trade.get("bd"),
                "id": trade.get("id"),
                "oi": trade.get("oi"),
                "existing": bool(trade.get("existing") or trade.get("h", False)),
            }
            if self.on_trade is not None:
                if asyncio.iscoroutinefunction(self.on_trade):
                    await self.on_trade(out)
                else:
                    # ИСПРАВЛЕНИЕ: прямой вызов для синхронных callback (queue.put).
                    # Предотвращает RuntimeError "cannot schedule new futures after shutdown" при Ctrl+C.
                    self.on_trade(out)
            return
        if "message" in obj or "httpCode" in obj:
            self._subscribed.add(obj.get("requestGuid", ""))
            if self.on_status is not None:
                if asyncio.iscoroutinefunction(self.on_status):
                    await self.on_status(obj)
                else:
                    self.on_status(obj)

    async def _connect_once(self):
        self._token_mgr.get_access_token()
        async with ws_connect(ALOR_WS_URL) as ws:
            self._ws = ws
            self._subscribed.clear()
            for ticker in self.tickers:
                await self._subscribe_one(ws, ticker)
            async for message in ws:
                if not self._running:
                    break
                await self._dispatch_message(message)

    async def run_async(self):
        self._running = True
        attempt = 0
        try:
            while self._running:
                try:
                    await self._connect_once()
                    attempt = 0
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if not self._running:
                        break
                    attempt += 1
                    delay = min(WS_RECONNECT_BASE_SEC * (2 ** (attempt - 1)), WS_RECONNECT_MAX_SEC)
                    print(f"[AlorStream] разрыв: {type(e).__name__}: {e}; реконнект через {delay:.1f}с")
                    await asyncio.sleep(delay)
        finally:
            self._running = False

    def stop(self):
        self._running = False

    def run_sync(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.run_async())
        except KeyboardInterrupt:
            self.stop()
        finally:
            loop.close()

    def run_in_thread(self):
        t = threading.Thread(target=self.run_sync, name="alor-stream", daemon=True)
        t.start()
        return t, self


if __name__ == "__main__":
    import sys
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["SBER", "GAZP", "LKOH"]
    try:
        refresh = load_env_value("ALOR_REFRESH_TOKEN")
    except (FileNotFoundError, KeyError) as e:
        print(f"❌ {e}"); sys.exit(1)
    count = {"n": 0, "hist": 0}
    def _cb(trade):
        count["n"] += 1
        if trade["existing"]: count["hist"] += 1
        tag = "HIST" if trade["existing"] else "LIVE"
        print(f"[{tag}] {trade['symbol']:6s} {trade['side']:4s} qty={trade['qty']:<5} price={trade['price']} (всего {count['n']}, ист {count['hist']})")
    client = AlorStreamClient(refresh_token=refresh, tickers=tickers, depth=20, on_trade=_cb)
    try:
        client.run_sync()
    except KeyboardInterrupt:
        print(f"\nОстанов. Всего: {count['n']} (ист {count['hist']})")