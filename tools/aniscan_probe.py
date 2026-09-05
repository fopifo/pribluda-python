"""
Приблуда на python — зонд API aniscan.ru: качает образец robot-histories
в data/aniscan_sample.json и печатает структуру JSON.
Секреты — из .env (ANISCAN_COOKIES, ANISCAN_XSRF). Только читает/качает.
"""
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / ".env")

COOKIES = os.getenv("ANISCAN_COOKIES", "")
XSRF = os.getenv("ANISCAN_XSRF", "")


def main():
    if not COOKIES:
        print("Нет ANISCAN_COOKIES в .env")
        sys.exit(1)
    r = requests.get(
        "https://aniscan.ru/api/robot-histories",
        params={"page": 0, "size": 3, "sort": "createDttm,desc"},
        headers={
            "accept": "application/json, text/plain, */*",
            "x-xsrf-token": XSRF,
            "referer": "https://aniscan.ru/robot-history/table",
        },
        cookies=dict(c.split("=", 1) for c in COOKIES.split("; ") if "=" in c),
        timeout=30,
    )
    print(f"status={r.status_code}")
    out = BASE / "data" / "aniscan_sample.json"
    out.write_text(r.text, encoding="utf-8")
    print(f"сохранено: {out}")
    print(json.dumps(r.json(), ensure_ascii=False, indent=1)[:3000])


if __name__ == "__main__":
    main()