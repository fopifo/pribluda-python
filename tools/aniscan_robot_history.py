"""
Скачивание истории роботов из Aniscan за указанную дату.
Использует cookies из .env для авторизации.
Сохраняет результат в data/aniscan_robots_YYYY-MM-DD.jsonl
"""
import os
import sys
import json
import requests
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent.parent
ENV_FILE = BASE / ".env"
DATA_DIR = BASE / "data"

def get_aniscan_cookies():
    """Читает ANISCAN_COOKIES из .env"""
    if not ENV_FILE.exists():
        raise FileNotFoundError(".env не найден")
    with open(ENV_FILE, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line.startswith("ANISCAN_COOKIES="):
                cookie_str = line.split("=", 1)[1].strip().strip('"\'')
                cookies = {}
                for item in cookie_str.split(";"):
                    if "=" in item:
                        k, v = item.split("=", 1)
                        cookies[k.strip()] = v.strip()
                return cookies
    raise ValueError("ANISCAN_COOKIES не найдены в .env")

def download_robot_history(date_str: str):
    """Скачивает историю роботов за дату."""
    cookies = get_aniscan_cookies()
    
    # Реальный URL из DevTools
    url = "https://aniscan.ru/api/robot-histories"
    
    # Параметры запроса
    params = {
        "page": 0,
        "size": 1000,  # Увеличили с 50 до 1000 для получения всех записей
        "sort": "createDttm,desc"
    }
    
    # Заголовки из curl
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "ru",
        "dnt": "1",
        "priority": "u=1, i",
        "referer": "https://aniscan.ru/robot-history/table",
        "sec-ch-ua": '"Not-A.Brand";v="99", "Google Chrome";v="157", "Chromium";v="157"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36",
        "x-xsrf-token": cookies.get("XSRF-TOKEN", "")
    }
    
    print(f"[*] Запрос к Aniscan API: {url}")
    print(f"[*] Параметры: page={params['page']}, size={params['size']}")
    
    response = requests.get(url, params=params, cookies=cookies, headers=headers, timeout=30)
    
    print(f"[*] Статус ответа: {response.status_code}")
    if response.status_code != 200:
        print(f"[!] Ошибка HTTP {response.status_code}")
        print(f"[!] Ответ: {response.text[:300]}")
        return False
        
    try:
        data = response.json()
    except json.JSONDecodeError:
        print("[!] Ответ не является JSON")
        print(f"Первые 300 символов: {response.text[:300]}")
        return False

    # Сохраняем в jsonl
    output_file = DATA_DIR / f"aniscan_robots_{date_str}.jsonl"
    count = 0
    
    # Aniscan возвращает объект с content (Spring Data REST)
    if isinstance(data, dict):
        records = data.get("content", [])
    elif isinstance(data, list):
        records = data
    else:
        records = []

    with open(output_file, "w", encoding="utf-8") as f:
        for record in records:
            # Добавляем метаданные
            record["_downloaded_at"] = datetime.now().isoformat()
            record["_source_date"] = date_str
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
            
    print(f"\n[+] ✅ Успешно сохранено {count} записей")
    print(f"[+] Файл: {output_file}")
    print(f"[+] Размер: {output_file.stat().st_size / 1024:.1f} KB")
    
    # Показываем пример первой записи
    if records:
        print(f"\n[~] Пример первой записи:")
        print(json.dumps(records[0], ensure_ascii=False, indent=2)[:500])
    
    return True

if __name__ == "__main__":
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    try:
        download_robot_history(date)
    except Exception as e:
        print(f"\n[X] ❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()