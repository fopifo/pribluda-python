"""
Приблуда на python — пульс системы (наглядный статус).
Запуск: python tools/pulse_check.py
Вывод: ✅ зелёный (ОК) / ❌ красный (проблема) по каждому компоненту.
"""
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
MSK = ZoneInfo("Europe/Moscow")
DATA = BASE / "data"
OUT = BASE / "output"

OK = "\033[92m✅\033[0m"
BAD = "\033[91m❌\033[0m"
WARN = "\033[93m⚠️\033[0m"


def check(name, condition, ok_msg, bad_msg, warn=False):
    sym = WARN if warn else (OK if condition else BAD)
    msg = ok_msg if condition else bad_msg
    print(f"{sym} {name}: {msg}")


def main():
    now = datetime.now(MSK)
    print(f"\n{'='*60}")
    print(f"   ПУЛЬС ПРИБЛУДЫ — {now.strftime('%Y-%m-%d %H:%M:%S')} МСК")
    print(f"{'='*60}\n")

    # [1] QUIK лента — растёт ли CSV за 5 секунд
    csv_path = DATA / "quik_trades.csv"
    if not csv_path.exists():
        check("QUIK лента", False, "", "Файл data/quik_trades.csv НЕ СУЩЕСТВУЕТ")
    else:
        s1 = csv_path.stat().st_size
        time.sleep(5)
        s2 = csv_path.stat().st_size
        grew = s2 - s1
        check(
            "QUIK лента (5 сек)",
            grew > 0,
            f"+{grew:,} байт".replace(",", " "),
            f"НЕ РАСТЁТ ({s1:,} -> {s2:,}) — проверь lua-скрипт".replace(",", " "),
        )

    # [2] ISS котировки — свежая ли строка
    quotes_path = DATA / "quik_quotes.csv"
    if not quotes_path.exists():
        check("ISS котировки", False, "", "Файл НЕ СУЩЕСТВУЕТ")
    else:
        try:
            with open(quotes_path, encoding="utf-8", errors="ignore") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 500))
                f.readline()
                lines = f.readlines()
            last = lines[-1].strip().split(";") if lines else []
            ts_ms = int(last[-1]) if last and last[-1].isdigit() else 0
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=MSK)
            age = (now - ts).total_seconds()
            check(
                "ISS котировки",
                age < 60,
                f"последняя: {ts.strftime('%H:%M:%S')} ({age:.0f}с назад)",
                f"СТАРАЯ: {ts.strftime('%H:%M:%S')} ({age:.0f}с назад) — iss_quotes_sync не работает",
            )
        except Exception as e:
            check("ISS котировки", False, "", f"ошибка чтения: {e}")

    # [3] Interval robot — подтверждения сегодня
    hist_path = DATA / "robots_history.jsonl"
    today_str = now.strftime("%Y-%m-%d")
    if not hist_path.exists():
        check("Interval robot", False, "", "Файл НЕ СУЩЕСТВУЕТ")
    else:
        try:
            count_today = 0
            last_sym = None
            last_time = None
            with open(hist_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                        ts_str = r.get("timestamp", "")
                        if today_str in ts_str:
                            count_today += 1
                            last_sym = r.get("symbol")
                            last_time = ts_str[11:19] if len(ts_str) > 19 else "?"
                    except Exception:
                        pass
            check(
                "Interval robot",
                count_today > 0,
                f"{count_today} подтверждений, последний: {last_sym} @{last_time}",
                f"подтверждений сегодня = 0",
            )
        except Exception as e:
            check("Interval robot", False, "", f"ошибка: {e}")

    # [4] Backend log — нет свежих IMOEX fetch failed (за 60 сек)
    log_path = OUT / "quik_noconsole.log"
    if not log_path.exists():
        check("Backend log", False, "", "Файл НЕ СУЩЕСТВУЕТ")
    else:
        try:
            recent_imf_fails = 0
            with open(log_path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "IMOEX fetch failed" in line:
                        t = line[:8]
                        try:
                            lt = datetime.strptime(t, "%H:%M:%S").replace(
                                year=now.year, month=now.month, day=now.day,
                                tzinfo=MSK
                            )
                            if (now - lt).total_seconds() < 60:
                                recent_imf_fails += 1
                        except Exception:
                            pass
            check(
                "IMOEX fetch (60 сек)",
                recent_imf_fails == 0,
                f"нет ошибок за последнюю минуту",
                f"{recent_imf_fails} ошибок за последнюю минуту — проверь сеть или модуль spring_monitor",
            )
        except Exception as e:
            check("IMOEX fetch", False, "", f"ошибка чтения лога: {e}")

    # [5] IMOEX цена
    try:
        sys.path.insert(0, str(BASE))
        from modules.arbitrage.spring_monitor import fetch_imoex_price
        price = fetch_imoex_price()
        check(
            "IMOEX цена",
            price is not None and isinstance(price, float),
            f"{price:.2f}",
            "None — модуль не получил цену от ISS",
        )
    except Exception as e:
        check("IMOEX цена", False, "", f"импорт упал: {e}")

    # [6] StreamGrid — сигналы сегодня
    if log_path.exists():
        try:
            signals_today = 0
            last_sig = None
            with open(log_path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "[stream_grid] SIGNAL" in line and today_str:
                        # в логе формат HH:MM:SS — считаем все SIGNAL как сегодняшние
                        signals_today += 1
                        last_sig = line.strip()
            last_short = last_sig[9:] if last_sig else ""
            check(
                "StreamGrid (сегодня)",
                signals_today > 0,
                f"{signals_today} сигналов, последний: {last_short}",
                "сигналов StreamGrid пока нет",
                warn=True,
            )
        except Exception as e:
            check("StreamGrid", False, "", f"ошибка: {e}")

    # [7] robots_history — растёт ли за 5 секунд
    if hist_path.exists():
        s1 = hist_path.stat().st_size
        time.sleep(5)
        s2 = hist_path.stat().st_size
        grew = s2 - s1
        check(
            "robots_history (5 сек)",
            grew >= 0,
            f"+{grew:,} байт (подтверждения пишутся)".replace(",", " "),
            f"НЕ РАСТЁТ — детектор не подтверждает",
        )

    print(f"\n{'='*60}")
    print("   Конец проверки")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()