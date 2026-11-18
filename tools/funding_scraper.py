"""
tools/funding_scraper.py — парсер ежедневного фандинга по вечным фьючерсам MOEX.

Алгоритм:
1. Получает список всех фьючерсов с ISS API вместе с лотами (один запрос).
2. Фильтрует только те, что заканчиваются на 'F' (вечники).
3. Для каждого парсит HTML карточки moex.com/ru/derivatives/perpetual-futures/<TICKER>.
4. Извлекает два значения фандинга:
   - [0] = текущий индикативный (или '-' если клиринг не прошёл)
   - [1] = итоговый за прошлый клиринг
5. Для каждого сохраняет:
   - indicated / settled (сырые строки из HTML)
   - lot (из ISS) — чтобы в боте считать сумму за контракт
   - indicated_rub / settled_rub (float) — если удаётся распарсить, иначе null

Использование:
    python tools/funding_scraper.py              # за сегодня
    python tools/funding_scraper.py 2026-11-17   # за конкретную дату
"""
import json
import sys
import time
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import requests
except ImportError:
    print("[!] нет requests — выполни: pip install requests")
    sys.exit(1)

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
MSK = ZoneInfo("Europe/Moscow")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
}

FUNDING_PATTERN = re.compile(
    r'Фандинг, руб\.</div></div><div data-section="value"[^>]*><div[^>]*data-section="text">([^<]+)<'
)


class Progress:
    def __init__(self, total, label):
        self.total = max(total, 1)
        self.label = label
        self.done = 0
        self.t0 = time.time()

    def update(self, n=1):
        self.done += n
        pct = min(self.done * 100 // self.total, 100)
        dt = max(time.time() - self.t0, 1e-9)
        speed = self.done / dt
        eta = (self.total - self.done) / max(speed, 1.0)
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        print(
            f"\r[{self.label}] [{bar}] {pct:3d}% | {self.done}/{self.total} | ETA {eta:.0f}с",
            end="", flush=True
        )

    def close(self):
        print()


def parse_rub(s):
    """'0,54604' -> 0.54604 или None."""
    if s is None or s == '-' or s.strip() == '':
        return None
    try:
        return float(s.replace(',', '.').replace(' ', '').replace('\xa0', ''))
    except Exception:
        return None


def get_perpetual_futures_with_lots():
    """Возвращает список {secid: lot} для всех вечных фьючерсов (оканчиваются на 'F')."""
    print("[1/2] Получаю список фьючерсов + лоты из ISS API...")
    url = ("https://iss.moex.com/iss/engines/futures/markets/forts/securities.json"
           "?iss.only=securities&securities.columns=SECID,SECNAME,LOTSIZE,LASTTRADEDATE")
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[!] Ошибка ISS API: {e}")
        return {}

    sec_block = data.get("securities", {})
    cols = sec_block.get("columns", [])
    rows = sec_block.get("data", [])

    try:
        i_secid = cols.index("SECID")
        i_lot = cols.index("LOTSIZE")
    except ValueError:
        print("[!] В ответе ISS нет нужных колонок (SECID/LOTSIZE)")
        return {}

    out = {}
    for row in rows:
        if len(row) <= max(i_secid, i_lot):
            continue
        secid = str(row[i_secid] or "")
        if not secid.endswith("F"):
            continue
        lot = row[i_lot]
        try:
            lot = int(lot) if lot is not None else None
        except Exception:
            lot = None
        out[secid] = lot

    print(f"  Всего фьючерсов на ISS: {len(rows)}, вечных (на 'F'): {len(out)}")
    return out


def parse_funding_html(ticker):
    url = f"https://www.moex.com/ru/derivatives/perpetual-futures/{ticker}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return None, None
        html = r.text
        matches = FUNDING_PATTERN.findall(html)
        if len(matches) >= 2:
            return matches[0], matches[1]
        elif len(matches) == 1:
            return matches[0], None
        return None, None
    except Exception:
        return None, None


def main():
    today = datetime.now(MSK).date().isoformat()
    date_arg = sys.argv[1] if len(sys.argv) > 1 else today
    DATA.mkdir(exist_ok=True)

    print("=" * 70)
    print(" ПАРСЕР ФАНДИНГА ВЕЧНЫХ ФЬЮЧЕРСОВ MOEX")
    print("=" * 70)
    print(f"Дата: {date_arg}\n")

    lots = get_perpetual_futures_with_lots()
    if not lots:
        print("[!] Список вечных фьючерсов пуст.")
        sys.exit(1)

    tickers = sorted(lots.keys())
    print(f"\n[2/2] Парсю HTML карточки для {len(tickers)} вечников...")
    prog = Progress(len(tickers), "парсинг")

    results = {}
    for ticker in tickers:
        indicated_raw, settled_raw = parse_funding_html(ticker)
        ind_ok = indicated_raw and indicated_raw != '-'
        set_ok = settled_raw and settled_raw != '-'
        results[ticker] = {
            "indicated": indicated_raw if ind_ok else "ЕЩЁ НЕТ РЕЗУЛЬТАТА",
            "settled":   settled_raw   if set_ok else "ЕЩЁ НЕТ РЕЗУЛЬТАТА",
            "indicated_rub": parse_rub(indicated_raw) if ind_ok else None,
            "settled_rub":   parse_rub(settled_raw)   if set_ok else None,
            "lot": lots.get(ticker),
        }
        prog.update()
        time.sleep(0.1)

    prog.close()

    out_file = DATA / f"funding_{date_arg}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print()
    print("=" * 70)
    print(f" РЕЗУЛЬТАТЫ ({len(results)} тикеров, отсортировано по итоговому фандингу)")
    print("=" * 70)
    print(f"{'TICKER':<12} {'ЛОТ':>6} {'ИНДИКАТИВНЫЙ':>14} {'ИТОГОВЫЙ':>14} {'ИТОГ×ЛОТ':>12}")
    print("-" * 62)

    rows = sorted(results.items(),
                  key=lambda kv: (kv[1]["settled_rub"] is None,
                                  -(kv[1]["settled_rub"] or 0)))
    for ticker, v in rows:
        lot = v["lot"] or 0
        ind_s = v["indicated"] if len(v["indicated"]) <= 14 else v["indicated"][:14]
        set_s = v["settled"] if len(v["settled"]) <= 14 else v["settled"][:14]
        if v["settled_rub"] is not None and lot:
            per_contract = f"{v['settled_rub'] * lot:+.2f}"
        else:
            per_contract = "—"
        print(f"{ticker:<12} {lot:>6} {ind_s:>14} {set_s:>14} {per_contract:>12}")

    print()
    print(f"[OK] Сохранено: {out_file}")
    print()
    print("Следующий шаг: интеграция в MAX-бот (раздел 'Арбитраж').")


if __name__ == "__main__":
    main()