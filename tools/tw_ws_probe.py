"""
tools/tw_ws_probe.py — зонд WebSocket T-Widgets (mindstocks).
Формат исходящих подтверждён из браузера (F12->WS->Messages, стрелки вверх):
    ["5","info"], ["5","futoi"], ["5","imoex"], ["5","info:<uid>"]
Значит подписка на роботов = ["5","setka"].
uid берём из JWT-токена (поле "uid"), не вписываем руками.

Запуск:
  python tools/tw_ws_probe.py <URL> [N]        # только дефолтный поток
  python tools/tw_ws_probe.py <URL> [N] setka  # + подписки (info/futoi/imoex/setka)
"""
import sys
import os
import json
import time
import base64
import asyncio
from collections import Counter

try:
    import websockets
except ImportError:
    print("[!] нет websockets — выполни: pip install websockets")
    sys.exit(1)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_token():
    p = os.path.join(BASE, ".env")
    tok = ""
    if os.path.exists(p):
        for line in open(p, encoding="utf-8-sig"):
            if line.strip().startswith("MINDSTOCKS_TOKEN="):
                tok = line.strip().split("=", 1)[1].strip().strip('"\'')
                break
    return tok


def parse_uid_from_jwt(tok):
    """JWT payload = вторая часть, base64url. Вёрстка: {"...","uid":<n>}."""
    try:
        payload = tok.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data.get("uid")
    except Exception:
        return None


def build_subscribes(uid):
    """Исходящие как в браузере: ["5","<канал>"]. Добавляем setka для роботов."""
    subs = [
        json.dumps(["5", "info"]),
        json.dumps(["5", "futoi"]),
        json.dumps(["5", "imoex"]),
    ]
    if uid is not None:
        subs.append(json.dumps(["5", f"info:{uid}"]))
    subs.append(json.dumps(["5", "setka"]))  # <-- канал роботов/сеток
    return subs


async def probe(url, n_frames, subscribe_setka, timeout_sec=25):
    print(f"[*] подключаюсь к: {url[:110]}...")
    frames = []
    events = Counter()
    try:
        async with websockets.connect(
            url, open_timeout=10, close_timeout=3,
            additional_headers={
                "Origin": "https://www.tbank-online.com",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            },
        ) as ws:
            print("[+] соединение установлено")
            if subscribe_setka:
                tok = url.split("token=", 1)[1] if "token=" in url else load_token()
                uid = parse_uid_from_jwt(tok)
                print(f"[*] uid из токена: {uid}")
                for s in build_subscribes(uid):
                    await ws.send(s)
                    print(f"[>] отправил: {s}")
            t1 = time.time()
            while len(frames) < n_frames and time.time() - t1 < timeout_sec:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=timeout_sec)
                except asyncio.TimeoutError:
                    print(f"[~] тишина >{timeout_sec}с")
                    break
                frames.append(msg)
                try:
                    arr = json.loads(msg)
                    if isinstance(arr, list) and len(arr) >= 2 and isinstance(arr[1], str):
                        events[arr[1]] += 1
                except Exception:
                    events["<RAW>"] += 1
                sys.stdout.write(f"\r    фреймов: {len(frames)}/{n_frames} | "
                                 f"события: {dict(events)}")
                sys.stdout.flush()
            sys.stdout.write("\n")
    except Exception as e:
        print(f"[x] ошибка: {type(e).__name__}: {str(e)[:150]}")
    return frames, events


def show(frames, n):
    seen = set()
    print(f"\n=== ПРИМЕРЫ ФРЕЙМОВ (по event) ===")
    for f in frames:
        try:
            arr = json.loads(f)
            ev = arr[1] if isinstance(arr, list) and len(arr) >= 2 else "?"
        except Exception:
            ev = "<RAW>"
        if ev in seen:
            continue
        seen.add(ev)
        short = f if len(f) <= 600 else f[:600] + "...[TRUNC]"
        print(f"\n--- event={ev} ---\n{short}")
        if len(seen) >= n:
            break


async def main():
    if len(sys.argv) < 2:
        print("использование: python tools/tw_ws_probe.py <URL> [N] [setka]")
        sys.exit(1)
    url = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    sub = "setka" in sys.argv[3:]

    frames, events = await probe(url, n, sub)
    if frames:
        print(f"\n[счётчик событий] {dict(events)}")
        show(frames, 10)
        if events.get("setka") or events.get("robot") or events.get("robots") or events.get("grid"):
            print("\n[OK] канал роботов ПОЛУЧЕН — следующим шагом пишу capture.")
        elif sub:
            print("\n[!] setka-фреймов нет даже после ['5','setka']. "
                  "Возможно роботы на ДРУГОМ WS (в Network их много). "
                  "Пришли мне Request URL того соединения, где в Messages "
                  "есть входящие с тикерами роботов.")
        else:
            print("\n[!] это дефолтный поток (новости). Добавь флаг setka.")
    else:
        print("\n[x] фреймов нет.")


if __name__ == "__main__":
    asyncio.run(main())