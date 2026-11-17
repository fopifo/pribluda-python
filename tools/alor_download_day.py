"""
tools/alor_download_day.py — скачивание истории сделок за день из Алор API.
Исправлено (2026-11-17): динамическое определение instrumentGroup (TQBR/RFUD/CETS)
для предотвращения 404 ошибок на фьючерсах и валюте.
"""
import sys
import json
import time
import requests
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"
MSK = ZoneInfo("Europe/Moscow")

# Маппинг исключений или эвристика для определения площадки
def get_instrument_group(symbol: str) -> str:
    s = symbol.upper()
    # Фьючерсы
    if s in ("CNYRUBF", "SRU6SRZ6", "SPZ6", "SiU6") or s.startswith("CNY-") or s.endswith("F"):
        return "RFUD"
    # Валюта
    if "TOM" in s or "TOD" in s or s in ("CNYRUB_TOM", "CNY000000TOD"):
        return "CETS"
    # Акции по умолчанию
    return "TQBR"

def load_active_tickers():
    settings_path = BASE / "ticker_settings.json"
    if not settings_path.exists():
        return []
    with open(settings_path, encoding="utf-8-sig") as f:
        data = json.load(f)
    return [sym for sym, cfg in data.items() if cfg.get("active", True)]

def download_day(date_str: str):
    print(f"[alor] дата {date_str}, определяем площадки...")
    tickers = load_active_tickers()
    print(f"[alor] тикеров для скачивания: {len(tickers)}")
    
    # Парсим дату для timestamp
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    ts_from = int(datetime(dt.year, dt.month, dt.day, 0, 0, 0, tzinfo=MSK).timestamp())
    ts_to = int(datetime(dt.year, dt.month, dt.day, 23, 59, 59, tzinfo=MSK).timestamp())
    
    for i, sym in enumerate(tickers, 1):
        board = get_instrument_group(sym)
        out_file = DATA_DIR / f"{sym}_{date_str}.json"
        
        print(f"[{i}/{len(tickers)}] {sym} ({board}): ", end="", flush=True)
        
        if out_file.exists() and out_file.stat().st_size > 100:
            print("уже есть, пропускаю")
            continue
            
        print("качаю...", end="", flush=True)
        
        all_trades = []
        offset = 0
        limit = 50000
        
        try:
            while True:
                url = (f"https://api.alor.ru/md/v2/Securities/MOEX/{sym}/alltrades/history"
                       f"?instrumentGroup={board}&from={ts_from}&to={ts_to}&limit={limit}&offset={offset}")
                resp = requests.get(url, timeout=15)
                if resp.status_code == 404:
                    print(f"\n  ошибка: 404 (возможно, делистинг или неверная площадка {board})")
                    break
                resp.raise_for_status()
                data = resp.json()
                
                if not data:
                    break
                    
                all_trades.extend(data)
                
                if len(data) < limit:
                    break
                offset += limit
                time.sleep(0.1) # защита от rate limit
                
            if all_trades:
                # Сортируем по времени для хронологического порядка
                all_trades.sort(key=lambda x: x.get("timestamp", 0))
                with open(out_file, "w", encoding="utf-8") as f:
                    json.dump(all_trades, f, ensure_ascii=False)
                print(f"\n  сохранено {len(all_trades)} сделок -> {out_file.name}")
            else:
                print(f"\n  сохранено 0 сделок -> {out_file.name}")
                
        except Exception as e:
            print(f"\n  ошибка: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        target_date = sys.argv[1]
    else:
        target_date = (datetime.now(MSK).date()).strftime("%Y-%m-%d")
        
    DATA_DIR.mkdir(exist_ok=True)
    download_day(target_date)
    print("[alor] Готово.")