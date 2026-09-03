"""
Приблуда на python — компаратор: наши роботы против роботов T-Widgets.

Сравнивает:
  - наш поток:        data/robots_history.jsonl
  - эталон T-Widgets: data/tw_robots_YYYY-MM-DD.jsonl (перехватчик OnRobots2)

!!! ВСЕ ВРЕМЕНА — МИЛЛИСЕКУНДЫ (epoch ms), БЕЗ КОНВЕРСИЙ !!!
  Наши:      детектор пишет start_ms/end_ms/interval_ms напрямую из ленты
             QUIK (в ленте время уже в мс). Строки старого формата без
             start_ms пропускаются.
  T-Widgets: interval — уже мс; start/end — ISO-строки (Z) -> epoch ms.

Метрики:
  TP — робот есть и у нас, и у T-Widgets (совпали по критерию)
  FP — наш робот, которого T-Widgets не видит
  FN — робот T-Widgets, которого мы не видим
  Precision = TP/(TP+FP), Recall = TP/(TP+FN)

Критерий совпадения (наш r и tw-робот w):
  1) ticker и side равны;
  2) окна пересекаются с допуском TIME_TOL_MS;
  3) INT_LO <= r.interval_ms / w.interval_ms <= INT_HI;
  4) лоты пересекаются с допуском QTY_TOL (наш диапазон расширяется).

Использование (из корня проекта):
    python research/tw_compare.py             # последний tw_robots_*.jsonl
    python research/tw_compare.py 2026-09-03  # конкретная дата

Ничего не меняет — только читает и печатает.
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
MSK = ZoneInfo("Europe/Moscow")

TIME_TOL_MS = 120_000      # допуск по пересечению окон (2 мин), мс
INT_LO, INT_HI = 0.7, 1.3  # допуск по отношению интервалов
QTY_TOL = 0.5              # расширение диапазона лотов, 50%
DETAIL_LIMIT = 30          # сколько строк деталей печатать на категорию


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
            f"\r[tw_compare] {self.label} [{bar}] {pct:3d}% | "
            f"{self.done // 1024}/{self.total // 1024}KB | "
            f"{speed / 1024:.0f}KB/s | ETA {eta:.0f}s",
            end="",
            flush=True,
        )

    def close(self):
        print()


def load_tw(path):
    """tw_robots_*.jsonl -> список записей в мс (active+completed, дедуп по id)."""
    out = {}
    skipped = 0
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
                skipped += 1
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
                            "end_skips": a.get("endSkips"),
                            "state": state,
                        }
    p.close()
    if skipped:
        print(f"[tw_compare] tw: пропущено битых строк: {skipped}")
    return list(out.values())


def load_ours(path, date_str):
    """robots_history.jsonl за дату (МСК) -> список записей в мс.
    Строки старого формата (без start_ms) пропускаются."""
    out = []
    legacy = 0
    p = Progress(path.stat().st_size, "our")
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            p.update(len(line))
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            start_ms = r.get("start_ms")
            end_ms = r.get("end_ms")
            interval_ms = r.get("interval_ms")
            if start_ms is None or end_ms is None or interval_ms is None:
                legacy += 1
                continue
            day = datetime.fromtimestamp(start_ms / 1000, tz=MSK).date().isoformat()
            if day != date_str:
                continue
            qv = r.get("qty_variants") or []
            if not qv:
                continue
            out.append({
                "ticker": r.get("symbol"),
                "side": r.get("side"),
                "interval_ms": float(interval_ms),
                "qty_min": min(qv),
                "qty_max": max(qv),
                "start_ms": int(start_ms),
                "end_ms": int(end_ms),
                "repeats": r.get("repeats"),
            })
    p.close()
    if legacy:
        print(f"[tw_compare] our: строк старого формата (без мс) пропущено: {legacy}")
    return out


def match(r, w):
    """Критерий совпадения нашего робота r и робота T-Widgets w (всё в мс)."""
    if r["ticker"] != w["ticker"] or r["side"] != w["side"]:
        return False
    if r["start_ms"] > (w["end_ms"] or 0) + TIME_TOL_MS:
        return False
    if (w["start_ms"] or 0) > r["end_ms"] + TIME_TOL_MS:
        return False
    if r["interval_ms"] > 0 and w["interval_ms"] > 0:
        ratio = r["interval_ms"] / w["interval_ms"]
        if not (INT_LO <= ratio <= INT_HI):
            return False
    r_lo = r["qty_min"] * (1 - QTY_TOL)
    r_hi = r["qty_max"] * (1 + QTY_TOL)
    if not (r_lo <= w["qty_max"] and w["qty_min"] <= r_hi):
        return False
    return True


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
        print("[tw_compare] Нет файлов data/tw_robots_*.jsonl")
        sys.exit(1)
    tw_file = files[-1]
    date_str = tw_file.name.replace("tw_robots_", "").replace(".jsonl", "")
    print(f"[tw_compare] эталон T-Widgets: {tw_file.name} (дата {date_str})")

    tw = load_tw(tw_file)
    print(f"[tw_compare] роботов T-Widgets (уник. ticker+side+id): {len(tw)}")

    ours_path = DATA / "robots_history.jsonl"
    if not ours_path.exists():
        print("[tw_compare] Нет data/robots_history.jsonl")
        sys.exit(1)
    ours = load_ours(ours_path, date_str)
    print(f"[tw_compare] наших роботов (в мс) за {date_str}: {len(ours)}")

    tw_list = tw
    used = set()
    tp, fp, fn = [], [], []
    for r in sorted(ours, key=lambda x: x["start_ms"]):
        hit = None
        for i, w in enumerate(tw_list):
            if i in used:
                continue
            if match(r, w):
                hit = i
                break
        if hit is not None:
            used.add(hit)
            tp.append((r, tw_list[hit]))
        else:
            fp.append(r)
    for i, w in enumerate(tw_list):
        if i not in used:
            fn.append(w)

    prec = len(tp) / (len(tp) + len(fp)) if (tp or fp) else 0.0
    rec = len(tp) / (len(tp) + len(fn)) if (tp or fn) else 0.0

    print()
    print("=" * 70)
    print("СРАВНЕНИЕ: наша приблуда против T-Widgets (все времена в мс)")
    print("=" * 70)
    print(f"TP: {len(tp)}   FP: {len(fp)}   FN: {len(fn)}")
    print(f"Precision: {prec:.1%}   Recall: {rec:.1%}")

    if tp:
        print(f"\n--- TP (совпали, первые {DETAIL_LIMIT}) ---")
        for r, w in tp[:DETAIL_LIMIT]:
            print(
                f"  {r['ticker']:6} {r['side']:4} "
                f"наш int={r['interval_ms'] / 1000:.1f}s qty=[{r['qty_min']},{r['qty_max']}] "
                f"| tw int={w['interval_ms'] / 1000:.1f}s qty=[{w['qty_min']},{w['qty_max']}] "
                f"окно {fmt_ms(r['start_ms'])}-{fmt_ms(r['end_ms'])}"
            )

    if fp:
        print(f"\n--- FP (наши, которых T-Widgets не видит, первые {DETAIL_LIMIT}) ---")
        for r in fp[:DETAIL_LIMIT]:
            print(
                f"  {r['ticker']:6} {r['side']:4} "
                f"int={r['interval_ms'] / 1000:.1f}s qty=[{r['qty_min']},{r['qty_max']}] "
                f"повт={r['repeats']} окно {fmt_ms(r['start_ms'])}-{fmt_ms(r['end_ms'])}"
            )

    if fn:
        print(f"\n--- FN (T-Widgets видит, мы нет, первые {DETAIL_LIMIT}) ---")
        for w in fn[:DETAIL_LIMIT]:
            print(
                f"  {w['ticker']:6} {w['side']:4} "
                f"int={w['interval_ms'] / 1000:.1f}s qty=[{w['qty_min']},{w['qty_max']}] "
                f"count={w['count']} skips={w['end_skips']} "
                f"окно {fmt_ms(w['start_ms'])}-{fmt_ms(w['end_ms'])}"
            )

    print("\n[tw_compare] Готово.")


if __name__ == "__main__":
    main()