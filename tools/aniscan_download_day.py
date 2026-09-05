"""
Приблуда на python — загрузка истории роботов aniscan.ru (второй эталон).
Листает API robot-histories (сортировка createDttm desc) и собирает все
события с createDttm >= ГРАНИЦА в data/aniscan_history.jsonl
(одна строка JSON на событие).

Использование:
    python tools/aniscan_download_day.py             # граница 2026-09-03
    python tools/aniscan_download_day.py 2026-09-04  # своя граница

Секреты из .env: ANISCAN_COOKIES, ANISCAN_XSRF.
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

import json  #

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / ".env")
OUT = BASE / "data" / "aniscan_history.jsonl"
MSK = timezone(timedelta(hours=3))
PAGE = 100


def parse_ts(rec):
    return datetime.fromisoformat(rec["createDttm"]).timestamp()


def main():
    args = sys.argv[1:]
    start_str = args[0] if args else "2026-09-03"
    boundary = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=MSK).timestamp()

    raw = os.getenv("ANISCAN_COOKIES", "")
    xsrf = os.getenv("ANISCAN_XSRF", "")
    if not raw:
        print("Нет ANISCAN_COOKIES в .env")
        sys.exit(1)
    cookies = dict(c.split("=", 1) for c in raw.split("; ") if "=" in c)

    sess = requests.Session()
    sess.headers.update({
        "accept": "application/json, text/plain, */*",
        "x-xsrf-token": xsrf,
        "referer": "https://aniscan.ru/robot-history/table",
    })
    sess.cookies.update(cookies)

    records = []
    t0 = None
    page = 0
    stop = False
    wall0 = time.time()
    last = 0.0

    while not stop:
        r = sess.get("https://aniscan.ru/api/robot-histories",
                     params={"page": page, "size": PAGE, "sort": "createDttm,desc"},
                     timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        for rec in data:
            ts = parse_ts(rec)
            if t0 is None:
                t0 = ts
            if ts < boundary:
                stop = True
                break
            records.append(rec)
        tc = parse_ts(data[-1])
        page += 1

        span = max(t0 - boundary, 1.0)
        pct = min(int(max(t0 - tc, 0) * 100 / span), 99)
        now = time.time()
        if now - last > 0.25:
            last = now
            el = max(now - wall0, 1e-9)
            speed = len(records) / el
            eta = el * (100 - pct) / max(pct, 1)
            bar = "#" * (pct // 5) + "." * (20 - pct // 5)
            print(f"\r[aniscan] [{bar}] {pct:3d}% | стр={page} зап={len(records)} | "
                  f"{speed:.0f}/с | ETA {eta:.0f}с", end="", flush=True)
        time.sleep(0.15)

    print()
    with open(OUT, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[aniscan] готово: {len(records)} событий -> {OUT}")




if __name__ == "__main__":
    main()