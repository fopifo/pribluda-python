# -*- coding: utf-8 -*-
# Онлайн-агрегатор сущностей v5: склеивает принты ленты моста в серии ударов
# и присваивает каждой серии cluster_id. ТОЛЬКО ЧИТАЕТ ленту моста.
# Пишет: data/entities_YYYY-MM-DD.jsonl (закрытые серии),
#        data/entities_live.json   (снапшот живых серий для GUI/оверлея).
# v5: адаптивный допуск цены - tol растёт с возрастом серии (1x за 5 минут,
#     потолок 10x базового 0.05%): медленные роботы перестают рвать серии;
#     отсечка блоков: медиана лотов тикера за день (выборка до LOT_CAP сделок),
#     серия выбрасывается целиком, если avg_lot > BLOCK_MULT x медианы тикера
#     (крупный блок типа VTBR-12x104540 отсеивается, роботы остаются).
# v4: MIN_N = 8 (серии короче восьми сделок - шум, не публикуем нигде);
#     стартовый фильтр 5 с: первые секунды после запуска принты пропускаем,
#     чтобы не собирать ложные серии из первых попавшихся сделок;
#     тег big ужесточён: n >= 50 И total >= 1000.
# v3: CD и NEXT считаются по локальным часам агрегатора (момент получения принта),
#     INT и JMS - по меткам времени самих сделок.
import json, os, time, statistics, sys

ROOT = r"C:\Users\Public\MI_CODES\dpNEW"
DATA = os.path.join(ROOT, "data")
MAX_GAP_S   = 90.0     # разрыв, после которого серия закрыта (по локальным часам)
PRICE_TOL   = 0.0005   # базовый допуск цены внутри серии (0.05%)
TOL_GROW_MIN = 5.0     # возраст серии в минутах, за который допуск удваивается (1x -> 2x)
TOL_MAX_X   = 10.0     # потолок допуска: 10x базового (0.5%)
MIN_N       = 8        # серии короче - шум, в файл и в живой вид не пишем
STARTUP_S   = 5.0      # после старта столько секунд принты пропускаем
BLOCK_MULT  = 50.0     # avg_lot серии > BLOCK_MULT x медианы лотов тикера = блок
LOT_CAP     = 20000    # выборка лотов на тикер: столько последних сделок помним
REFRESH     = 1.0      # период отрисовки и записи снапшота, сек
MAX_ROWS    = 22       # строк в консольной таблице

def now_ms(): return int(time.time() * 1000)

def today(): return time.strftime("%Y-%m-%d")

class Series:
    def __init__(self, cid, sym, side, ts, price, lot, tloc):
        self.cid = cid; self.sym = sym; self.side = side
        self.t0 = ts; self.tl = ts          # метки сделок (биржа) - для INT/JMS
        self.l0 = tloc; self.ll = tloc      # локальные часы - для CD/NEXT и разрывов
        self.prices = [price]; self.lots = [lot]; self.ints = []

    def fits(self, ts, price, tloc):
        if not self.ints:
            return True
        if (tloc - self.ll) > MAX_GAP_S * 1000:
            return False
        pmin, pmax = min(self.prices), max(self.prices)
        base = max(pmax * PRICE_TOL, 1e-9)
        age_ms = tloc - self.l0
        tol = base * min(TOL_MAX_X, 1.0 + age_ms / (TOL_GROW_MIN * 60000.0))
        return (pmin - tol) <= price <= (pmax + tol)

    def add(self, ts, price, lot, tloc):
        self.ints.append(ts - self.tl)
        self.tl = ts; self.ll = tloc
        self.prices.append(price); self.lots.append(lot)

    def avg_lot(self):
        return sum(self.lots) / len(self.lots) if self.lots else 0.0

    def snapshot(self, now):
        n = len(self.lots)
        total = float(sum(self.lots))
        avg = total / n if n else 0.0
        lot_med = statistics.median(self.lots) if self.lots else 0.0
        int_ms = statistics.median(self.ints) if self.ints else 0.0
        j3 = self.ints[-3:]
        jms = sum(j3) / len(j3) if j3 else 0.0
        cv = None
        if len(self.ints) >= 4:
            m = statistics.mean(self.ints)
            if m > 0: cv = statistics.pstdev(self.ints) / m
        tags = []
        if n >= 8 and len(set(self.prices)) <= 2: tags.append("iceberg")
        if cv is not None and cv < 0.2 and n >= 5: tags.append("twap")
        if n >= 50 and total >= 1000: tags.append("big")
        next_ms = self.ll + int_ms          # по локальным часам агрегатора
        span_min = max((self.tl - self.t0) / 60000.0, 1.0 / 60.0)
        return {
            "cluster_id": self.cid, "symbol": self.sym, "side": self.side,
            "n": n, "total_qty": round(total, 4), "avg_lot": round(avg, 4),
            "lot_med": round(lot_med, 2),
            "price_first": self.prices[0], "lpp": self.prices[-1],
            "t_first": self.t0, "t_last": self.tl,
            "int_s": round(int_ms / 1000.0, 2), "jms_ms": round(jms, 1),
            "cv": None if cv is None else round(cv, 3),
            "vpm": round(total / span_min, 1),
            "next_ms": next_ms, "cd_s": round(max(0.0, (next_ms - now) / 1000.0), 1),
            "active": True, "tags": tags,
        }

class Aggregator:
    def __init__(self):
        self.active = {}            # (sym, side) -> Series
        self.seq = {}               # (sym, side) -> счётчик серий
        self.lot_hist = {}          # sym -> выборка лотов сделок дня (до LOT_CAP)
        self.t_start = now_ms()     # стартовый фильтр: отсчёт от запуска
        self.out_path = os.path.join(DATA, f"entities_{today()}.jsonl")

    def next_cid(self, sym, side):
        k = (sym, side)
        self.seq[k] = self.seq.get(k, 0) + 1
        return f"{sym}-{('B' if side == 'buy' else 'S' if side == 'sell' else 'X')}-{today()}-{self.seq[k]:04d}"

    def observe(self, sym, lot):
        h = self.lot_hist.setdefault(sym, [])
        if len(h) < LOT_CAP:
            h.append(lot)

    def ticker_med(self, sym):
        h = self.lot_hist.get(sym)
        return statistics.median(h) if h else None

    def blocked(self, s):
        med = self.ticker_med(s.sym)
        if med is None:
            return False
        return s.avg_lot() > BLOCK_MULT * med

    def process(self, rec):
        if now_ms() - self.t_start < STARTUP_S * 1000:
            return                  # стартовый фильтр: первые секунды не трогаем
        sym = rec.get("symbol") or ""
        side = rec.get("side") or "?"
        price = float(rec.get("price") or 0)
        lot = float(rec.get("qty") or 0)
        ts = int(rec.get("timestamp") or now_ms())
        tloc = int(rec.get("_recv_ms") or now_ms())
        if not sym or price <= 0 or lot <= 0:
            return
        self.observe(sym, lot)
        k = (sym, side)
        cur = self.active.get(k)
        if cur is not None and cur.fits(ts, price, tloc):
            cur.add(ts, price, lot, tloc)
        else:
            if cur is not None:
                self.finalize(cur)
            self.active[k] = Series(self.next_cid(sym, side), sym, side, ts, price, lot, tloc)

    def finalize(self, s):
        if len(s.lots) < MIN_N or self.blocked(s):
            return
        snap = s.snapshot(now_ms())
        line = json.dumps(snap, ensure_ascii=False)
        try:
            with open(self.out_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def stale_cleanup(self, now):
        for k in list(self.active.keys()):
            s = self.active[k]
            if now - s.ll > MAX_GAP_S * 1000:
                self.finalize(s)
                del self.active[k]

    def _live_rows(self, now):
        keep = [s for s in self.active.values()
                if len(s.lots) >= MIN_N and not self.blocked(s)]
        rows = [s.snapshot(now) for s in keep]
        rows.sort(key=lambda r: -r["t_last"])
        return rows

    def live_snapshot(self, now):
        return {"updated_ms": now, "clusters": self._live_rows(now)}

    def render(self, now):
        try: os.system("cls")
        except Exception: pass
        t = time.strftime("%H:%M:%S")
        rows = self._live_rows(now)
        print(f"=== AGGREGATOR v5  {t}  |  живых серий: {len(rows)}  ===")
        print(f"{'CD':>5} {'TICKER':<12} {'QTY':>7} {'INT':>6} {'NEXT':>8} "
              f"{'JMS':>7} {'LPP':>10} {'VPM':>9} {'LEN':>5}  TAGS")
        for r in rows[:MAX_ROWS]:
            nxt = time.strftime("%H:%M:%S", time.localtime(r["next_ms"] / 1000.0))
            print(f"{r['cd_s']:>4.0f} {r['symbol']:<12} {r['lot_med']:>7.0f} "
                  f"{r['int_s']:>5.1f} {nxt:>8} {r['jms_ms']:>6.0f} "
                  f"{r['lpp']:>10.4g} {r['vpm']:>9.1f} {r['n']:>5}  {','.join(r['tags'])}")
        if not rows:
            print("(пока нет активных серий)")

def follow(agg):
    cur = None; off = 0; f = None
    while True:
        now = now_ms()
        fname = os.path.join(DATA, f"pbsdk_trades_{today()}.jsonl")
        if cur != fname:
            if f: f.close()
            f = None; cur = fname; off = 0
        if f is None:
            try:
                f = open(fname, "rb")
            except OSError:
                pass
        if f is not None:
            f.seek(off)
            chunk = f.read()
            off = f.tell()
            if chunk:
                recv_ms = now_ms()
                for raw in chunk.split(b"\n"):
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        rec["_recv_ms"] = recv_ms
                        agg.process(rec)
                    except Exception:
                        pass
        agg.stale_cleanup(now)
        snap = agg.live_snapshot(now)
        try:
            tmp = os.path.join(DATA, "entities_live.tmp")
            with open(tmp, "w", encoding="utf-8") as g:
                json.dump(snap, g, ensure_ascii=False)
            os.replace(tmp, os.path.join(DATA, "entities_live.json"))
        except OSError:
            pass
        agg.render(now)
        time.sleep(REFRESH / 2.0)

if __name__ == "__main__":
    print("Entities aggregator v5: MIN_N=8, старт-фильтр 5с, "
          "адаптивный допуск, отсечка блоков (50x медианы тикера). Ctrl+C - стоп.")
    agg = Aggregator()
    try:
        follow(agg)
    except KeyboardInterrupt:
        for s in list(agg.active.values()):
            agg.finalize(s)
        print("Стоп. Активные серии записаны.")
