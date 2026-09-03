"""
Приблуда на python — сводка по роботам T-Widgets: кого мы режем на входе.

Читает перехваченные снапшоты data/tw_robots_*.jsonl, агрегирует роботов
T-Widgets по профилю (ticker, side, лоты, интервал) и сверяет с нашим
min_qty из ticker_settings.json.

!!! ЕДИНИЦЫ: внутри всё в МИЛЛИСЕКУНДАХ (epoch ms) !!!
  interval у T-Widgets — уже мс; start/end — ISO (Z) -> epoch ms.
  В секунды/минуты перевожу ТОЛЬКО при печати и для корзины группировки.

Корзины:
  bursts   — interval < 2 с или count < 4 (всплески/шум, вне анализа);
  РЕЖЕМ    — весь диапазон лотов ниже нашего min_qty (мы не видим вообще);
  ЧАСТИЧНО — часть вариантов лотов ниже min_qty;
  ПРОХОДИТ — лоты проходят min_qty (причина расхождения в другом);
  НЕ СЛЕДИМ — тикера нет в ticker_settings.json (или не активен).

Вывод:
  1) топ-40 профилей по персистентности (число серий, суммарная жизнь);
  2) кандидаты на точечное понижение min_qty: РЕЖЕМ и серий >= 3,
     с предложением нового min_qty (maxLots - 2, как в check_min_qty_vs_ref).

Использование (из корня проекта):
    python research/tw_fn_summary.py             # последний tw_robots_*.jsonl
    python research/tw_fn_summary.py 2026-09-03  # конкретная дата

Ничего не меняет — только читает и печатает.
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from core.ticker_settings import load_settings

DATA = BASE / "data"
MSK = ZoneInfo("Europe/Moscow")

MIN_INTERVAL_MS = 2000.0   # TW-роботы быстрее этого — bursts (вне анализа)
MIN_COUNT = 4              # минимум сделок у TW-робота
DETAIL_LIMIT = 40          # строк в основной таблице
CAND_MIN_SERIES = 3        # минимум серий для кандидата на правку min_qty


def iso_to_ms(s):
    """ISO-строка (с Z) -> epoch ms."""
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return int(datetime.fromisoformat(s).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


class Progress:
    """Прогресс-бар по байтам файла. ASCII, без юникода."""

    def __init__(self, total, label):
        self.total = max(total, 1)
        self.label = label
        self.done = 0
        self.t0 = time.time()
        self.last = 0.0

    def update(self, n):
        self.done += n
        now = time.time()
        if now - self.last < 0.25 and self.done < self.total:
            return
        self.last = now
        pct = min(self.done * 100 // self.total, 100)
        dt = max(now - self.t0, 1e-9)
        speed = self.done / dt
        eta = (self.total - self.done) / max(speed, 1.0)
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        print(
            f"\r[tw_fn_summary] {self.label} [{bar}] {pct:3d}% | "
            f"{self.done // 1024}/{self.total // 1024}KB | "
            f"{speed / 1024:.0f}KB/s | ETA {eta:.0f}s",
            end="",
            flush=True,
        )

    def close(self):
        print()


def load_tw(path):
    """tw_robots_*.jsonl -> список роботов в мс (active+completed, дедуп по id)."""
    out = {}
    p = Progress(path.stat().st_size, "tw ")
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            p.update(len(line))
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            payload = rec.get("payload") or {}
            for rob in payload.get("robots") or []:
                ticker = rob.get("ticker")
                if not ticker:
                    continue
                for state in ("active", "completed"):
                    for a in rob.get(state) or []:
                        end_ms = iso_to_ms(a.get("end"))
                        key = (ticker, "buy" if a.get("isBuy") else "sell", a.get("id"))
                        old = out.get(key)
                        if old is not None and (old["end_ms"] or 0) >= (end_ms or 0):
                            continue
                        out[key] = {
                            "ticker": ticker,
                            "side": key[1],
                            "id": a.get("id"),
                            "interval_ms": float(a.get("interval") or 0),
                            "qty_min": a.get("minLots") or 0,
                            "qty_max": a.get("maxLots") or 0,
                            "start_ms": iso_to_ms(a.get("start")),
                            "end_ms": end_ms,
                            "count": a.get("count"),
                        }
    p.close()
    return list(out.values())


def fmt_ms(ms):
    if ms is None:
        return "--:--:--"
    return datetime.fromtimestamp(ms / 1000, tz=MSK).strftime("%H:%M:%S")


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    files = sorted(DATA.glob("tw_robots_*.jsonl"))
    if arg:
        files = [f for f in files if arg in f.name]
    if not files:
        print("[tw_fn_summary] Нет файлов data/tw_robots_*.jsonl")
        sys.exit(1)
    tw_file = files[-1]
    date_str = tw_file.name.replace("tw_robots_", "").replace(".jsonl", "")
    print(f"[tw_fn_summary] файл: {tw_file.name} (дата {date_str})")

    tw = load_tw(tw_file)
    real = [
        w for w in tw
        if (w["interval_ms"] or 0) >= MIN_INTERVAL_MS and (w["count"] or 0) >= MIN_COUNT
    ]
    print(f"[tw_fn_summary] роботов всего: {len(tw)}, bursts вне анализа: {len(tw) - len(real)}, реальных: {len(real)}")

    settings = load_settings()

    # Группировка по профилю: (ticker, side, лоты, интервал в сек)
    groups = {}
    for w in real:
        int_s = round(w["interval_ms"] / 1000)
        key = (w["ticker"], w["side"], w["qty_min"], w["qty_max"], int_s)
        g = groups.setdefault(key, {"series": 0, "max_count": 0, "life_ms": 0, "last_end": 0})
        g["series"] += 1
        g["max_count"] = max(g["max_count"], w["count"] or 0)
        g["life_ms"] += max((w["end_ms"] or 0) - (w["start_ms"] or 0), 0)
        g["last_end"] = max(g["last_end"], w["end_ms"] or 0)

    rows = []
    for (ticker, side, qmin, qmax, int_s), g in groups.items():
        ov = settings.get(ticker)
        min_qty = ov.get("min_qty") if ov else None
        if min_qty is None:
            verdict = "НЕ СЛЕДИМ"
        elif qmax < min_qty:
            verdict = "РЕЖЕМ"
        elif qmin < min_qty:
            verdict = "ЧАСТИЧНО"
        else:
            verdict = "ПРОХОДИТ"
        rows.append({
            "ticker": ticker, "side": side, "qmin": qmin, "qmax": qmax,
            "int_s": int_s, "series": g["series"], "max_count": g["max_count"],
            "life_ms": g["life_ms"], "last_end": g["last_end"],
            "min_qty": min_qty, "verdict": verdict,
        })

    rows.sort(key=lambda r: (-r["series"], -r["life_ms"]))

    print()
    print("=" * 100)
    print(f"ПРОФИЛИ РОБОТОВ T-WIDGETS за {date_str} (топ-{DETAIL_LIMIT} по персистентности)")
    print("=" * 100)
    print(f"{'TICKER':6} {'SIDE':4} {'QTY':>10} {'INT':>5} {'серий':>5} {'max_cnt':>7} "
          f"{'жизнь':>8} {'посл.акт':>8} {'min_qty':>7}  ВЕРДИКТ")
    for r in rows[:DETAIL_LIMIT]:
        print(
            f"{r['ticker']:6} {r['side']:4} [{r['qmin']},{r['qmax']}]".ljust(23) +
            f"{r['int_s']:>4}с {r['series']:>5} {r['max_count']:>7} "
            f"{r['life_ms'] / 60000:>6.1f}м {fmt_ms(r['last_end']):>8} "
            f"{str(r['min_qty']) if r['min_qty'] is not None else '—':>7}  {r['verdict']}"
        )

    # Кандидаты на точечное понижение min_qty
    cands = [r for r in rows if r["verdict"] == "РЕЖЕМ" and r["series"] >= CAND_MIN_SERIES]
    cands.sort(key=lambda r: (-r["series"], -r["life_ms"]))
    print()
    print("=" * 100)
    print(f"КАНДИДАТЫ НА ТОЧЕЧНОЕ ПОНИЖЕНИЕ min_qty (РЕЖЕМ и серий >= {CAND_MIN_SERIES}): {len(cands)}")
    print("=" * 100)
    if not cands:
        print("  нет")
    for r in cands:
        proposal = max(r["qmax"] - 2, 1)
        print(
            f"  {r['ticker']:6} {r['side']:4} qty=[{r['qmin']},{r['qmax']}] int={r['int_s']}с "
            f"серий={r['series']} max_cnt={r['max_count']} жизнь={r['life_ms'] / 60000:.1f}м "
            f"посл.акт={fmt_ms(r['last_end'])} | min_qty {r['min_qty']} -> {proposal}"
        )

    # Сводка по вердиктам
    print()
    from collections import Counter
    cnt = Counter(r["verdict"] for r in rows)
    print("Сводка по вердиктам профилей:", dict(cnt))
    print("\n[tw_fn_summary] Готово. Правки min_qty — отдельным шагом, после решения владельца.")


if __name__ == "__main__":
    main()