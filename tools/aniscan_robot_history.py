"""
Скачивание истории роботов Aniscan v2 (2026-11-10).
API не фильтрует по дате: отдаёт последние N событий по всем дням
(sort=createDttm,desc). Поэтому:
  - пагинация page=0,1,2... с остановкой на границе "позавчера";
  - сохраняем ТОЛЬКО today и yesterday, старше — выбрасываем;
  - yesterday: читаем существующий файл, выбрасываем мусор (не-yesterday
    даты), докачиваем до границы позавчера, дедуп по id, перезапись;
  - today: перезапись (день живой);
  - прогресс-бар по скачанным записям (правило 2026-08-26, ASCII).
Cookies из .env (ANISCAN_COOKIES). Перед запуском браузер должен быть
залогинен на aniscan.ru (правило 2026-09-05).
"""
import os
import sys
import json
import requests
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
ENV_FILE = BASE / ".env"
DATA_DIR = BASE / "data"
URL = "https://aniscan.ru/api/robot-histories"
PAGE_SIZE = 1000
MAX_PAGES = 40  # страховка от бесконечного цикла
MSK = ZoneInfo("Europe/Moscow")


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


def build_headers(cookies):
    """Заголовки из curl (байт-в-байт рабочего файла)."""
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
        "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/157.0.0.0 Safari/537.36"),
        "x-xsrf-token": cookies.get("XSRF-TOKEN", ""),
    }


def rec_date(rec):
    return (rec.get("createDttm") or "")[:10]


def load_yesterday_clean(path, yesterday_str):
    """Читает существующий yesterday-файл, оставляет ТОЛЬКО записи с датой
    yesterday (мусор других дат выбрасывается), дедуп по id.
    Возвращает (records, ids)."""
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
    """ASCII прогресс-бар (не ломается в PowerShell, правило 2026-09-08)."""
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
    cookies = get_aniscan_cookies()
    headers = build_headers(cookies)

    today = datetime.now(MSK).date()
    yesterday = today - timedelta(days=1)
    today_str = str(today)
    yesterday_str = str(yesterday)
    print(f"[*] today={today_str} yesterday={yesterday_str}")
    print(f"[*] сохраняем только эти две даты; старше — выбрасываем")

    # читаем существующий yesterday, очищаем от мусора
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
        resp = requests.get(URL, params=params, cookies=cookies,
                            headers=headers, timeout=30)
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
                    break  # sort desc: дальше только старше
        page += 1

    sys.stdout.write("\n")

    # yesterday: очищенные существующие + новые (дедуп уже гарантирован)
    y_final = y_existing + kept_yest_new
    with open(ypath, "w", encoding="utf-8") as f:
        for r in y_final:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # today: перезапись
    tpath = DATA_DIR / f"aniscan_robots_{today_str}.jsonl"
    with open(tpath, "w", encoding="utf-8") as f:
        for r in kept_today:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"[+] скачано записей: {downloaded} (страниц: {page})")
    print(f"[+] yesterday({yesterday_str}): было(чищено) {len(y_existing)} "
          f"+ новых {len(kept_yest_new)} = {len(y_final)}")
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