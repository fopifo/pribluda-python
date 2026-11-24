"""
tools/alor_download_day.py — скачивание истории сделок за день из Алор API.
Использует ALOR_REFRESH_TOKEN из .env для получения access-токена.
Защищено от 429 (Retry с бэкоффом) и 401 (чёткая диагностика).
Сохраняет всё в единый data/live_trades_YYYY-MM-DD.jsonl для replay_alor.py.

v2 (2026-11-19): исправлено определение площадки — убран глупый .endswith("F"),
который отправлял CHMF/DOMRF в RFUD (404); SiU6/MXZ6 добавлены явно (регистр).
"""
import sys
import json
import os
import time
import requests
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"
MSK = ZoneInfo("Europe/Moscow")

load_dotenv(BASE / ".env")
REFRESH_TOKEN = os.getenv("ALOR_REFRESH_TOKEN")
TOKEN_URL = "https://oauth.alor.ru/refresh"
API_URL = "https://api.alor.ru"

# Явный список фьючерсов RFUD (никаких .endswith("F") — он ломал CHMF/DOMRF)
RFUD_SYMBOLS = {"CNYRUBF", "SRU6SRZ6", "SPZ6", "SIU6", "MXZ6"}
# Явный список валюты CETS
CETS_SYMBOLS = {"CNYRUB_TOM", "CNY000000TOD"}


def get_instrument_group(symbol: str) -> str:
    s = symbol.upper()
    if s in RFUD_SYMBOLS or s.startswith("CNY-"):
        return "RFUD"
    if s in CETS_SYMBOLS or s.endswith("_TOM") or s.endswith("TOD"):
        return "CETS"
    return "TQBR"


def load_active_tickers():
    settings_path = BASE / "ticker_settings.json"
    if not settings_path.exists():
        return []
    with open(settings_path, encoding="utf-8-sig") as f:
        data = json.load(f)
    return [sym for sym, cfg in data.items() if cfg.get("active", True)]


class AlorAuth:
    def __init__(self):
        if not REFRESH_TOKEN:
            raise ValueError("Не найден ALOR_REFRESH_TOKEN в .env. Проверь файл!")
        self.session = requests.Session()
        retry = Retry(total=3, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503, 504])
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        resp = self.session.post(TOKEN_URL, params={"token": REFRESH_TOKEN}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        self.access_token = data.get("access_token") or data.get("AccessToken")
        if not self.access_token:
            raise ValueError("Не удалось получить access_token из ответа Алор")

    def get_headers(self):
        return {"Authorization": f"Bearer {self.access_token}"}


def download_day(date_str: str):
    print(f"[alor] дата {date_str}, определяем площадки...")
    tickers = load_active_tickers()
    print(f"[alor] тикеров для скачивания: {len(tickers)}")

    dt = datetime.strptime(date_str, "%Y-%m-%d")
    ts_from = int(datetime(dt.year, dt.month, dt.day, 0, 0, 0, tzinfo=MSK).timestamp())
    ts_to = int(datetime(dt.year, dt.month, dt.day, 23, 59, 59, tzinfo=MSK).timestamp())

    try:
        auth = AlorAuth()
    except Exception as e:
        print(f"\n[alor] ❌ Ошибка авторизации: {e}")
        print("   Действие: обнови ALOR_REFRESH_TOKEN в файле .env")
        sys.exit(1)

    out_file = DATA_DIR / f"live_trades_{date_str}.jsonl"
    total_trades = 0
    failed_tickers = []

    for i, sym in enumerate(tickers, 1):
        board = get_instrument_group(sym)
        print(f"[{i}/{len(tickers)}] {sym} ({board}): ", end="", flush=True)

        all_trades = []
        offset = 0
        limit = 50000

        try:
            while True:
                url = f"{API_URL}/md/v2/Securities/MOEX/{sym}/alltrades/history"
                params = {
                    "instrumentGroup": board,
                    "from": ts_from,
                    "to": ts_to,
                    "limit": limit,
                    "offset": offset,
                }
                resp = auth.session.get(url, headers=auth.get_headers(), params=params, timeout=15)

                if resp.status_code == 401:
                    print(f"\n  ❌ Ошибка 401: токен недействителен. Обнови .env")
                    sys.exit(1)

                if resp.status_code == 404:
                    print(f"\n  ⚠️ 404: делистинг/нет на площадке {board}")
                    break

                resp.raise_for_status()
                payload = resp.json()
                page = payload.get("list", [])
                if not page:
                    break

                all_trades.extend(page)
                offset += len(page)
                if len(page) < limit:
                    break

            if all_trades:
                all_trades.sort(key=lambda x: x.get("time", 0) or x.get("timestamp", 0))
                with open(out_file, "a", encoding="utf-8") as f:
                    for t in all_trades:
                        f.write(json.dumps(t, ensure_ascii=False) + "\n")
                total_trades += len(all_trades)
                print(f"✅ {len(all_trades)}")
            else:
                print("⚪ 0")

        except Exception as e:
            print(f"\n  ❌ Ошибка: {e}")
            failed_tickers.append(sym)

        time.sleep(0.2)

    print("-" * 50)
    print(f"[alor] ✅ Готово. Всего сохранено сделок: {total_trades}")
    print(f"[alor] 📁 Файл: {out_file.name}")
    if failed_tickers:
        print(f"[alor] ⚠️ Не удалось скачать: {', '.join(failed_tickers)}")


if __name__ == "__main__":
    target_date = sys.argv[1] if len(sys.argv) > 1 else datetime.now(MSK).strftime("%Y-%m-%d")
    DATA_DIR.mkdir(exist_ok=True)

    old_file = DATA_DIR / f"live_trades_{target_date}.jsonl"
    if old_file.exists():
        old_file.unlink()

    download_day(target_date)