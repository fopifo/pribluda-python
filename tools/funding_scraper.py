"""
tools/funding_scraper.py — парсер ежедневного фандинга по вечным фьючерсам MOEX.

Механика фандинга MOEX (подтверждена выпиской Т-Банка):
  положительный фандинг -> платят ПОКУПАТЕЛИ (лонг), получают ПРОДАВЦЫ (шорт).
  Итог по позиции со знаком pos (лонг +, шорт -):  funding = -pos * lot * settled.
  Пример: шорт 2 лота CNYRUBF, settled=0,0052, лот=1000 -> -(-2)*1000*0,0052 = +10,40 ₽
  (ровно payout за 17.11 из выписки).

ВАЖНО про количество лотов: позиция берётся из data/bridge_state.json — это
ТЕКУЩАЯ позиция моста, а фандинг начисляется на позицию НА МОМЕНТ КЛИРИНГА.
Если через ночь держал другое число лотов — итог будет по текущей позиции.
Точный расчёт требует историю позиций по датам (см. PROJECT_NOTES, задача).

Алгоритм:
1. Список фьючерсов + лоты из ISS API (колонка LOTVOLUME).
2. Фильтр вечников (оканчиваются на 'F').
3. Конкурентный парсинг HTML карточек (10 потоков).
4. Значения: [0] индикативный, [1] итоговый за прошлый клиринг.
5. Позиции из моста -> расчёт итога со знаком.
6. Блок «МОИ ПОЗИЦИИ ПО ФАНДИНГУ»: лоты × фандинг/контракт = итого.

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
from concurrent.futures import ThreadPoolExecutor, as_completed

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

MAX_WORKERS = 10


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
    print("[1/3] Получаю список фьючерсов + лоты из ISS API...")
    url = "https://iss.moex.com/iss/engines/futures/markets/forts/securities.json"
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

    if "SECID" not in cols or "LOTVOLUME" not in cols:
        print(f"[!] В ответе ISS нет нужных колонок. Доступные: {cols}")
        return {}

    i_secid = cols.index("SECID")
    i_lot = cols.index("LOTVOLUME")

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
            return ticker, None, None
        matches = FUNDING_PATTERN.findall(r.text)
        if len(matches) >= 2:
            return ticker, matches[0], matches[1]
        elif len(matches) == 1:
            return ticker, matches[0], None
        return ticker, None, None
    except Exception:
        return ticker, None, None


def load_bridge_positions():
    """Читает data/bridge_state.json -> {ticker: position} (текущая позиция моста)."""
    p = DATA / "bridge_state.json"
    if not p.exists():
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            state = json.load(f)
        syms = state.get("symbols", {})
        return {s: i.get("position", 0) for s, i in syms.items() if i.get("position", 0) != 0}
    except Exception:
        return {}


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
    print(f"\n[2/3] Парсю HTML карточки для {len(tickers)} вечников (потоков: {MAX_WORKERS})...")
    results = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(parse_funding_html, t): t for t in tickers}
        done_count = 0
        for future in as_completed(futures):
            ticker, indicated_raw, settled_raw = future.result()
            ind_ok = indicated_raw and indicated_raw != '-'
            set_ok = settled_raw and settled_raw != '-'
            results[ticker] = {
                "indicated": indicated_raw if ind_ok else "ЕЩЁ НЕТ РЕЗУЛЬТАТА",
                "settled":   settled_raw   if set_ok else "ЕЩЁ НЕТ РЕЗУЛЬТАТА",
                "indicated_rub": parse_rub(indicated_raw) if ind_ok else None,
                "settled_rub":   parse_rub(settled_raw)   if set_ok else None,
                "lot": lots.get(ticker),
            }
            done_count += 1
            pct = done_count * 100 // len(tickers)
            dt = max(time.time() - t0, 1e-9)
            eta = (len(tickers) - done_count) / max(done_count / dt, 1.0)
            bar = "#" * (pct // 5) + "." * (20 - pct // 5)
            print(f"\r[парсинг] [{bar}] {pct:3d}% | {done_count}/{len(tickers)} | ETA {eta:.0f}с", end="", flush=True)
    print()
    print(f"  Парсинг завершён за {time.time() - t0:.1f}с")

    print("\n[3/3] Считаем фандинг по твоим позициям...")
    positions = load_bridge_positions()
    my_positions = {}
    total_funding = 0.0
    for ticker, pos in positions.items():
        if ticker not in results:
            continue
        v = results[ticker]
        lot = v.get("lot") or 0
        settled = v.get("settled_rub")
        if settled is None or lot == 0:
            continue
        # Знак MOEX: лонг платит при +фандинге, шорт получает -> funding = -pos*lot*settled
        funding = -pos * lot * settled
        my_positions[ticker] = {
            "position": pos,
            "lot": lot,
            "funding_per_unit": settled,
            "funding_per_contract": settled * lot,
            "total_funding": funding,
        }
        total_funding += funding

    out_file = DATA / f"funding_{date_arg}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({"funding": results, "my_positions": my_positions,
                   "note": "позиция из моста (текущая); фандинг начисляется на позицию клиринга"},
                  f, ensure_ascii=False, indent=2)

    print()
    print("=" * 70)
    print(f" РЕЗУЛЬТАТЫ ({len(results)} тикеров, отсортировано по итоговому фандингу)")
    print("=" * 70)
    print(f"{'TICKER':<12} {'ЛОТ':>6} {'ИНДИКАТИВНЫЙ':>14} {'ИТОГОВЫЙ':>14} {'ИТОГ×ЛОТ':>12}")
    print("-" * 62)
    rows = sorted(results.items(),
                  key=lambda kv: (kv[1]["settled_rub"] is None, -(kv[1]["settled_rub"] or 0)))
    for ticker, v in rows:
        lot = v["lot"] or 0
        ind_s = v["indicated"] if len(v["indicated"]) <= 14 else v["indicated"][:14]
        set_s = v["settled"] if len(v["settled"]) <= 14 else v["settled"][:14]
        per_contract = f"{v['settled_rub'] * lot:+.2f}" if (v["settled_rub"] is not None and lot) else "—"
        print(f"{ticker:<12} {lot:>6} {ind_s:>14} {set_s:>14} {per_contract:>12}")

    if my_positions:
        print()
        print("=" * 70)
        print(f" 📊 МОИ ПОЗИЦИИ ПО ФАНДИНГУ ({len(my_positions)} тикеров)")
        print("=" * 70)
        print(f"{'TICKER':<12} {'ЛОТОВ(поз)':>10} {'ФАНД/КОНТРАКТ':>14} {'ИТОГО':>12}")
        print("-" * 52)
        for ticker in sorted(my_positions.keys()):
            mp = my_positions[ticker]
            emoji = "🟢" if mp["total_funding"] >= 0 else "🔴"
            print(f"{ticker:<12} {mp['position']:>+10.0f} {mp['funding_per_contract']:>+14.2f} "
                  f"{emoji} {mp['total_funding']:>+11.2f} ₽")
        total_emoji = "🟢" if total_funding >= 0 else "🔴"
        print("-" * 52)
        print(f"{'ИТОГО ПО ВСЕМ ПОЗИЦИЯМ:':<40} {total_emoji} {total_funding:>+11.2f} ₽")
        print()
        print("⚠️ Лоты взяты из моста (текущая позиция). Фандинг начисляется на позицию")
        print("   НА МОМЕНТ КЛИРИНГА. Если через ночь держал другое число — итог скорректируй.")
    else:
        print()
        print("[!] Открытых позиций по вечным фьючерсам нет (мост не отдаёт).")

    print()
    print(f"[OK] Сохранено: {out_file}")


if __name__ == "__main__":
    main()