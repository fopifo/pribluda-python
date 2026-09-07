"""
Приблуда на python — live polling aniscan.ru (роботы в реальном времени).
Polls API каждые POLL_SEC секунд, пишет НОВЫЕ INSERT/DELETE события
в data/aniscan_live.jsonl (APPEND, дедуп по (robot.id, createDttm))
и выводит их в stdout + файл output/aniscan_live.log.

Запуск: python tools/aniscan_live.py [poll_sec]
Ctrl+C — graceful stop.

При 401 (сессия протухла) — пишет понятное сообщение и ждёт обновления
cookies в .env + перезапуска.

Секреты из .env: ANISCAN_COOKIES, ANISCAN_XSRF.
"""
import os
import sys
import time
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / ".env")
LIVE_OUT = BASE / "data" / "aniscan_live.jsonl"
LOG_FILE = BASE / "output" / "aniscan_live.log"
MSK = timezone(timedelta(hours=3))
PAGE = 20


def now_msk():
    return datetime.now(MSK)


def parse_ts(rec):
    try:
        s = rec["createDttm"]
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp() * 1000
    except Exception:
        return None


def log(msg, tee_stdout=True):
    ts = now_msk().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    if tee_stdout:
        print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_existing_keys():
    keys = set()
    for fp in (LIVE_OUT,):
        if not fp.exists():
            continue
        try:
            for line in open(fp, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    rid = (r.get("robot") or {}).get("id")
                    dttm = r.get("createDttm")
                    if rid is not None and dttm is not None:
                        keys.add((rid, dttm))
                except Exception:
                    pass
        except Exception:
            pass
    return keys


def make_session():
    raw = os.getenv("ANISCAN_COOKIES", "")
    xsrf = os.getenv("ANISCAN_XSRF", "")
    if not raw or not xsrf:
        log("ERROR: ANISCAN_COOKIES или ANISCAN_XSRF не заданы в .env")
        return None, None
    cookies = dict(c.split("=", 1) for c in raw.split("; ") if "=" in c)
    sess = requests.Session()
    sess.headers.update({
        "accept": "application/json, text/plain, */*",
        "x-xsrf-token": xsrf,
        "referer": "https://aniscan.ru/robot-history/table",
    })
    sess.cookies.update(cookies)
    return sess, xsrf


def fmt_time(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, tz=MSK).strftime("%H:%M:%S")


def fmt_event(rec):
    ev = rec.get("eventType") or "?"
    r = rec.get("robot") or {}
    ticker = r.get("ticker") or "?"
    side = (r.get("operationType") or "?").lower()
    period = r.get("period") or 0
    qmin = r.get("minLot") or 0
    qmax = r.get("maxLot") or 0
    ts = parse_ts(rec)
    t = fmt_time(ts) if ts else "??:??:??"
    return f"{ev:6s} {ticker:6s} {side:4s} int={period:.0f}s qty=[{qmin},{qmax}] @{t}"


def main():
    poll_sec = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    LOG_FILE.parent.mkdir(exist_ok=True)

    log(f"[aniscan_live] старт, poll={poll_sec}s")
    log(f"[aniscan_live] LIVE_OUT={LIVE_OUT}")
    log(f"[aniscan_live] LOG_FILE={LOG_FILE}")

    existing = load_existing_keys
    keys = load_existing_keys()
    log(f"[aniscan_live] загружено существующих ключей: {len(keys)}")

    # Первый проход: не писать в файл, просто пометить существующие как seen,
    # чтобы не было спама старыми событиями при первом запуске.
    sess, xsrf = make_session()
    if sess is None:
        return
    try:
        r = sess.get("https://aniscan.ru/api/robot-histories",
                     params={"page": 0, "size": PAGE, "sort": "createDttm,desc"},
                     timeout=15)
        if r.status_code == 401:
            log("SESSION EXPIRED (401) — обнови cookies в .env и перезапусти")
            return
        r.raise_for_status()
        data = r.json()
        for rec in data:
            rid = (rec.get("robot") or {}).get("id")
            dttm = rec.get("createDttm")
            if rid is not None and dttm is not None:
                keys.add((rid, dttm))
        log(f"[aniscan_live] baseline: {len(data)} событий помечено как seen")
    except Exception as e:
        log(f"ERROR при baseline: {e}")
        return

    # Основной цикл
    try:
        while True:
            time.sleep(poll_sec)
            try:
                r = sess.get("https://aniscan.ru/api/robot-histories",
                             params={"page": 0, "size": PAGE, "sort": "createDttm,desc"},
                             timeout=15)
            except requests.exceptions.RequestException as e:
                log(f"network error: {e}")
                continue

            if r.status_code == 401:
                log("=" * 60)
                log("SESSION EXPIRED (401)")
                log("1. Открой aniscan.ru в браузере, залогинься")
                log("2. F12 -> Network -> любой /api/ запрос")
                log("3. Скопируй заголовки cookie: и x-xsrf-token:")
                log("4. Обнови .env (ANISCAN_COOKIES, ANISCAN_XSRF)")
                log("5. Перезапусти этот скрипт")
                log("=" * 60)
                return
            if r.status_code != 200:
                log(f"HTTP {r.status_code}: {r.text[:200]}")
                continue

            try:
                data = r.json()
            except Exception as e:
                log(f"json parse error: {e}")
                continue

            new_count = 0
            new_recs = []
            for rec in data:
                rid = (rec.get("robot") or {}).get("id")
                dttm = rec.get("createDttm")
                key = (rid, dttm) if rid is not None and dttm is not None else None
                if key is None or key in keys:
                    continue
                keys.add(key)
                new_recs.append(rec)
                new_count += 1

            if new_recs:
                # пишем новые
                try:
                    with open(LIVE_OUT, "a", encoding="utf-8") as f:
                        for rec in new_recs:
                            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                except Exception as e:
                    log(f"write error: {e}")
                # выводим
                log(f"[aniscan_live] +{new_count} новых:")
                for rec in new_recs:
                    log(f"  {fmt_event(rec)}")

    except KeyboardInterrupt:
        log("[aniscan_live] stop (Ctrl+C)")


if __name__ == "__main__":
    main()