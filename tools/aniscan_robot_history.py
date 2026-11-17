"""
Скачивание истории роботов Aniscan v3 (2026-11-17).
Теперь скрипт сам предлагает вставить curl-запрос из браузера для обновления cookies,
если они протухли или отсутствуют.
"""
import os
import sys
import json
import re
import requests
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
ENV_FILE = BASE / ".env"
DATA_DIR = BASE / "data"
URL = "https://aniscan.ru/api/robot-histories"
PAGE_SIZE = 1000
MAX_PAGES = 40
MSK = ZoneInfo("Europe/Moscow")

def print_instructions():
    print("=" * 70)
    print(" СКАЧИВАНИЕ ИСТОРИИ ANISCAN (v3)")
    print("=" * 70)
    print("1. Открой https://aniscan.ru/robot-history/table в браузере и залогинься.")
    print("2. Нажми F12 -> вкладка 'Network' (Сеть).")
    print("3. Обнови страницу (F5).")
    print("4. Найди запрос 'robot-histories', кликни правой кнопкой -> Copy -> Copy as cURL (bash).")
    print("5. Вставь этот curl-запрос ниже и нажми Enter.")
    print("   (Если .env уже актуален, просто нажми Enter, чтобы пропустить).")
    print("=" * 70)

def parse_curl(curl_text: str):
    """Извлекает cookies и xsrf-token из curl-запроса."""
    cookies = ""
    xsrf = ""
    
    # Ищем -b '...' или -b "..."
    m_cookie = re.search(r"-b\s+['\"]([^'\"]+)['\"]", curl_text, re.IGNORECASE)
    if m_cookie:
        cookies = m_cookie.group(1)
    else:
        # Иногда бывает -H 'cookie: ...'
        m_cookie_h = re.search(r"-H\s+['\"]cookie:\s*([^'\"]+)['\"]", curl_text, re.IGNORECASE)
        if m_cookie_h:
            cookies = m_cookie_h.group(1)

    # Ищем x-xsrf-token
    m_xsrf = re.search(r"-H\s+['\"]x-xsrf-token:\s*([^'\"]+)['\"]", curl_text, re.IGNORECASE)
    if m_xsrf:
        xsrf = m_xsrf.group(1).strip()
        
    return cookies, xsrf

def update_env(cookies: str, xsrf: str):
    """Обновляет или добавляет ANISCAN_COOKIES и ANISCAN_XSRF в .env"""
    if not ENV_FILE.exists():
        ENV_FILE.write_text("", encoding="utf-8")
    
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    new_lines = []
    updated_cookies = False
    updated_xsrf = False
    
    for line in lines:
        if line.strip().startswith("ANISCAN_COOKIES="):
            new_lines.append(f'ANISCAN_COOKIES="{cookies}"')
            updated_cookies = True
        elif line.strip().startswith("ANISCAN_XSRF="):
            new_lines.append(f'ANISCAN_XSRF="{xsrf}"')
            updated_xsrf = True
        else:
            new_lines.append(line)
            
    if not updated_cookies:
        new_lines.append(f'ANISCAN_COOKIES="{cookies}"')
    if not updated_xsrf:
        new_lines.append(f'ANISCAN_XSRF="{xsrf}"')
        
    ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print("[OK] .env обновлён новыми cookies и xsrf-token.")

def get_aniscan_credentials():
    """Запрашивает curl или берёт из .env"""
    print_instructions()
    curl_input = input("Вставь curl-запрос (или Enter для пропуска): ").strip()
    
    if curl_input:
        cookies, xsrf = parse_curl(curl_input)
        if cookies and xsrf:
            update_env(cookies, xsrf)
        else:
            print("[!] Не удалось распарсить cookies или x-xsrf-token из curl. Проверь формат.")
            sys.exit(1)
            
    # Читаем из .env (возможно, только что обновлённого)
    if not ENV_FILE.exists():
        raise FileNotFoundError(".env не найден")
        
    env_text = ENV_FILE.read_text(encoding="utf-8-sig")
    cookies = ""
    xsrf = ""
    for line in env_text.splitlines():
        line = line.strip()
        if line.startswith("ANISCAN_COOKIES="):
            cookies = line.split("=", 1)[1].strip().strip('"\'')
        elif line.startswith("ANISCAN_XSRF="):
            xsrf = line.split("=", 1)[1].strip().strip('"\'')
            
    if not cookies or not xsrf:
        raise ValueError("ANISCAN_COOKIES или ANISCAN_XSRF не найдены в .env. Запусти скрипт и вставь curl.")
        
    return cookies, xsrf

def build_headers(xsrf: str):
    return {
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
        "x-xsrf-token": xsrf,
    }

def rec_date(rec):
    return (rec.get("createDttm") or "")[:10]

def load_yesterday_clean(path, yesterday_str):
    records = []
    ids = set()
    if not path.exists():
        return records, ids
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec_date(r) == yesterday_str:
                rid = r.get("id")
                if rid and rid not in ids:
                    ids.add(rid)
                    records.append(r)
    return records, ids

def bar(done, total, rate, eta):
    if total and total > 0:
        pct = min(100.0, 100.0 * done / total)
        filled = int(pct // 5)
        line = (f"\r[aniscan] [{'#'*filled}{'.'*(20-filled)}] "
                f"{pct:3.0f}% | {done}/{total} | {rate:.0f}/с | ETA {eta:.0f}с")
    else:
        line = f"\r[aniscan] [{done} записей | {rate:.0f}/с | ETA ~{eta:.0f}с]"
    sys.stdout.write(line)
    sys.stdout.flush()

def download():
    cookies, xsrf = get_aniscan_credentials()
    headers = build_headers(xsrf)
    
    # Парсим строку cookies в словарь для requests
    cookie_dict = {}
    for item in cookies.split(";"):
        if "=" in item:
            k, v = item.split("=", 1)
            cookie_dict[k.strip()] = v.strip()
    
    today = (datetime.strptime(sys.argv[1], "%Y-%m-%d").date() if len(sys.argv) > 1 else datetime.now(MSK).date())
    yesterday = today - timedelta(days=1)
    today_str = str(today)
    yesterday_str = str(yesterday)
    
    print(f"[*] today={today_str} yesterday={yesterday_str}")
    print(f"[*] сохраняем только эти две даты; старше — выбрасываем")
    
    ypath = DATA_DIR / f"aniscan_robots_{yesterday_str}.jsonl"
    y_existing, y_ids = load_yesterday_clean(ypath, yesterday_str)
    print(f"[*] yesterday в файле после очистки мусора: {len(y_existing)} записей")
    
    kept_today = []
    kept_yest_new = []
    new_y_ids = set()
    page = 0
    downloaded = 0
    skipped_old = 0
    stop = False
    total_elements = None
    t0 = datetime.now()
    
    while page < MAX_PAGES and not stop:
        params = {"page": page, "size": PAGE_SIZE, "sort": "createDttm,desc"}
        resp = requests.get(URL, params=params, cookies=cookie_dict, headers=headers, timeout=30)
        
        if resp.status_code != 200:
            print(f"\n[!] HTTP {resp.status_code}: {resp.text[:300]}")
            print("[!] Сессия мертва? Обнови ANISCAN_COOKIES через F12 Network.")
            return False
            
        try:
            data = resp.json()
        except json.JSONDecodeError:
            print(f"\n[!] Ответ не JSON (HTML?): {resp.text[:300]}")
            print("[!] Обнови cookies из браузера (F12 -> Network -> robot-histories).")
            return False
            
        if total_elements is None and isinstance(data, dict):
            pg = data.get("page") or {}
            te = pg.get("totalElements")
            if isinstance(te, int):
                total_elements = te
                
        if isinstance(data, dict):
            content = data.get("content", [])
        elif isinstance(data, list):
            content = data
        else:
            content = []
            
        if not content:
            break
            
        downloaded += len(content)
        elapsed = max((datetime.now() - t0).total_seconds(), 0.001)
        rate = downloaded / elapsed
        remaining = (total_elements - downloaded) if total_elements else 0
        eta = max(remaining / rate, 0) if total_elements else 0
        bar(downloaded, total_elements, rate, eta)
        
        for rec in content:
            d = rec_date(rec)
            if d == today_str:
                rec["_downloaded_at"] = datetime.now(MSK).isoformat()
                rec["_source_date"] = today_str
                kept_today.append(rec)
            elif d == yesterday_str:
                rid = rec.get("id")
                if rid and rid not in y_ids and rid not in new_y_ids:
                    new_y_ids.add(rid)
                    rec["_downloaded_at"] = datetime.now(MSK).isoformat()
                    rec["_source_date"] = yesterday_str
                    kept_yest_new.append(rec)
            else:
                skipped_old += 1
                if d < yesterday_str:
                    stop = True
                    break
                    
        page += 1
        
    sys.stdout.write("\n")
    
    y_final = y_existing + kept_yest_new
    with open(ypath, "w", encoding="utf-8") as f:
        for r in y_final:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            
    tpath = DATA_DIR / f"aniscan_robots_{today_str}.jsonl"
    with open(tpath, "w", encoding="utf-8") as f:
        for r in kept_today:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            
    print(f"[+] скачано записей: {downloaded} (страниц: {page})")
    print(f"[+] yesterday({yesterday_str}): было(чищено) {len(y_existing)} + новых {len(kept_yest_new)} = {len(y_final)}")
    print(f"[+] today({today_str}): {len(kept_today)} (перезапись)")
    print(f"[+] выброшено старше yesterday: {skipped_old}")
    print(f"[+] файлы: {ypath.name}, {tpath.name}")
    return True

if __name__ == "__main__":
    try:
        download()
    except Exception as e:
        print(f"\n[X] ❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()