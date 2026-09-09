"""
Приблуда на python — метрики StreamGrid против aniscan за дату.
Гонит сырую ленту data/<T>_<дата>.json хронологически через StreamGrid и
считает ОБЕ цифры: Recall_strict (совпало/aniscan) и Precision_strict
(совпало/наших сигналов). По ревью п.3-5.
v2.1: min_repeats и tol из командной строки (свип порогов, ревью п.6);
FN-список (aniscan видит, мы нет) для чек-листа классов (CHMF-тип / SBER-тип).
v2.2 (2026-09-05): разделение метрик на "чистая сетка" и "burst" (ревью п.5).
v2.3 (2026-09-08): флаг --noisy для теста фильтра шумовых qty (SBER-паттерн).
"""
import json
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from modules.stream_grid import StreamGrid

MSK = timezone(timedelta(hours=3))

# Тикеры с burst-агрегацией (НЕ чистая сетка, из диагностики 2026-09-05)
# SBER: зазоры 13-828с, burst-агрегация
# X5: загрязнённая сетка (случайные сделки того же объёма)
# OZON: стабильной сетки нет (скринеры агрегируют burst-ы)
BURST_TICKERS = {'SBER', 'X5', 'OZON'}

NOISY_QTY_SHARE = 0.03  # 3% всех сделок дня одним и тем же qty — шумовой


def compute_noisy_qty(trades):
    """trades: [(ts, sym, side, qty), ...] — все сделки за день.
    Возвращает {sym: set(qty)} — объёмы, которые считаются шумовыми."""
    by_sym = defaultdict(list)
    for ts, sym, side, qty in trades:
        by_sym[sym].append(qty)
    result = {}
    for sym, qtys in by_sym.items():
        if not qtys:
            continue
        total = len(qtys)
        counts = Counter(qtys)
        noisy = {q for q, c in counts.items() if c / total >= NOISY_QTY_SHARE}
        result[sym] = noisy
    return result


class Progress:
    def __init__(self, total, label):
        self.total = max(total, 1)
        self.label = label
        self.done = 0
        self.t0 = time.time()
        self.last = 0.0

    def update(self, n=1):
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
        print(f"\r[grid] {self.label} [{bar}] {pct:3d}% | {self.done}/{self.total} | "
              f"{speed:.0f}/s | ETA {eta:.0f}s", end="", flush=True, file=sys.stderr)

    def close(self):
        print(file=sys.stderr)


def iso(s):
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp() * 1000
    except (ValueError, TypeError):
        return None


def load_aniscan(date_str):
    day0 = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=MSK).timestamp() * 1000
    day1 = day0 + 86400_000
    robots = {}
    with open(BASE / "data" / "aniscan_history.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            rid = (r.get("robot") or {}).get("id")
            if not rid:
                continue
            g = robots.setdefault(rid, {
                "ticker": r.get("ticker"), "side": (r.get("operationType") or "").lower(),
                "period": float(r.get("period") or 0), "qmin": r.get("minLot") or 0,
                "qmax": r.get("maxLot") or 0, "ins": None, "del": None})
            ts = iso(r.get("createDttm"))
            if r.get("eventType") == "INSERT":
                g["ins"] = ts if g["ins"] is None else min(g["ins"], ts)
            elif r.get("eventType") == "DELETE":
                g["del"] = ts if g["del"] is None else max(g["del"], ts)
    return [g for g in robots.values()
            if g["ins"] is not None and not ((g["del"] or 0) < day0 or g["ins"] > day1)]


def load_trades(date_str):
    out = []
    for fp in sorted((BASE / "data").glob(f"*_{date_str}.json")):
        sym = fp.name.replace(f"_{date_str}.json", "")
        try:
            data = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        for t in data:
            side = (t.get("side") or "").lower()
            q, ts = t.get("qty"), t.get("timestamp")
            if side in ("buy", "sell") and q and ts:
                out.append((float(ts), sym, side, int(q)))
    out.sort()
    return out


def fmt_ms(ms):
    return datetime.fromtimestamp(ms / 1000, tz=MSK).strftime("%H:%M")


def compute_metrics(an, dets, label):
    used, matched, ex, fn_ex = set(), 0, [], []
    for g in an:
        hit = None
        for i, w in enumerate(dets):
            if i in used or w["ticker"] != g["ticker"] or w["side"] != g["side"]:
                continue
            if w["start_ms"] > (g["del"] or 0) + 120000 or g["ins"] > w["end_ms"] + 120000:
                continue
            if g["period"] > 0 and not (0.7 <= w["period"] / g["period"] <= 1.3):
                continue
            if not (w["qty_min"] <= g["qmax"] * 1.5 and g["qmin"] * 0.5 <= w["qty_max"]):
                continue
            hit = i
            break
        if hit is not None:
            used.add(hit)
            matched += 1
            if len(ex) < 12:
                w = dets[hit]
                ex.append(f"  {g['ticker']:6} {g['side']:4} aniscan int={g['period']:.0f}s "
                          f"qty=[{g['qmin']},{g['qmax']}] | мы int={w['period']:.0f}s "
                          f"qty=[{w['qty_min']},{w['qty_max']}]")
        else:
            if len(fn_ex) < 12:
                fn_ex.append(f"  {g['ticker']:6} {g['side']:4} int={g['period']:.0f}s "
                             f"qty=[{g['qmin']},{g['qmax']}] окно "
                             f"{fmt_ms(g['ins'])}-{fmt_ms(g['del'] or g['ins'])}")
    rec = matched / len(an) if an else 0.0
    prec = matched / len(dets) if dets else 0.0
    return {
        "label": label,
        "aniscan": len(an),
        "ours": len(dets),
        "matched": matched,
        "recall": rec,
        "precision": prec,
        "examples": ex,
        "fn_examples": fn_ex,
    }


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else "2026-09-04"
    mr = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    tol = float(sys.argv[3]) if len(sys.argv) > 3 else 0.12
    noisy = "--noisy" in sys.argv
    an = load_aniscan(date_str)
    trades = load_trades(date_str)
    noisy_by_sym = compute_noisy_qty(trades) if noisy else {}
    if noisy:
        print(f"[noisy] включён фильтр шумовых qty (доля >={NOISY_QTY_SHARE})")
        for sym, qs in sorted(noisy_by_sym.items()):
            if qs:
                print(f"  {sym}: {sorted(qs)[:10]}...")
    sg = StreamGrid(min_repeats=mr, tol=tol, noisy_qty=noisy_by_sym)
    dets = []
    pb = Progress(len(trades), date_str)
    for ts, sym, side, qty in trades:
        d = sg.on_trade(sym, side, qty, ts)
        if d:
            dets.append(d)
        pb.update()
    pb.close()

    # Разделение: чистая сетка (CHMF-тип) vs burst (SBER-тип)
    an_clean = [g for g in an if g["ticker"] not in BURST_TICKERS]
    an_burst = [g for g in an if g["ticker"] in BURST_TICKERS]
    dets_clean = [d for d in dets if d["ticker"] not in BURST_TICKERS]
    dets_burst = [d for d in dets if d["ticker"] in BURST_TICKERS]

    m_all = compute_metrics(an, dets, "ВСЕ")
    m_clean = compute_metrics(an_clean, dets_clean, "ЧИСТАЯ СЕТКА (без burst)")
    m_burst = compute_metrics(an_burst, dets_burst, "BURST (SBER/X5/OZON)")

    print(f"[{date_str}] mr={mr} tol={tol} noisy={noisy}")
    print(f"\n=== {m_all['label']} ===")
    print(f"aniscan={m_all['aniscan']} наших={m_all['ours']} совпало={m_all['matched']}")
    print(f"Recall_strict={m_all['recall']:.1%}  Precision_strict={m_all['precision']:.1%}")
    print("--- совпали (первые 12) ---")
    print("\n".join(m_all["examples"]))
    print("--- aniscan видит, мы нет (первые 12) ---")
    print("\n".join(m_all["fn_examples"]))

    print(f"\n=== {m_clean['label']} ===")
    print(f"aniscan={m_clean['aniscan']} наших={m_clean['ours']} совпало={m_clean['matched']}")
    print(f"Recall_strict={m_clean['recall']:.1%}  Precision_strict={m_clean['precision']:.1%}")
    print("--- совпали (первые 12) ---")
    print("\n".join(m_clean["examples"]))

    print(f"\n=== {m_burst['label']} ===")
    print(f"aniscan={m_burst['aniscan']} наших={m_burst['ours']} совпало={m_burst['matched']}")
    print(f"Recall_strict={m_burst['recall']:.1%}  Precision_strict={m_burst['precision']:.1%}")


if __name__ == "__main__":
    main()