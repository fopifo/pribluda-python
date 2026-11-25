# -*- coding: utf-8 -*-
# Реплей агрегатора: прогон сохранённой ленты pbsdk_trades_YYYY-MM-DD.jsonl
# через ту же логику серий, что и в entities_aggregator, офлайн.
# Старт-фильтр отключён (лента историческая), _recv_ms заменён меткой сделки.
# Пишет: data/replay_entities_YYYY-MM-DD.jsonl и печатает сводку.
# Запуск: py entities_replay.py [YYYY-MM-DD]
import sys, os, json, time, collections

ROOT = r"C:\Users\Public\MI_CODES\dpNEW"
DATA = os.path.join(ROOT, "data")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import entities_aggregator as EA

class ReplayAgg(EA.Aggregator):
    def __init__(self):
        super().__init__()
        self.closed = []
        self.t_start = 0            # старт-фильтр не нужен: лента уже историческая
    def process(self, rec):
        ts = int(rec.get("timestamp") or 0)
        if ts:
            rec["_recv_ms"] = ts    # локальные часы заменяем меткой сделки
        super().process(rec)
    def finalize(self, s):
        if len(s.lots) >= EA.MIN_N:
            self.closed.append(s.snapshot(s.ll))

def run(date):
    path = os.path.join(DATA, "pbsdk_trades_%s.jsonl" % date)
    n_in = 0
    agg = ReplayAgg()
    with open(path, "rb") as f:
        for raw in f:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            n_in += 1
            agg.process(rec)
    for s in list(agg.active.values()):
        agg.finalize(s)
    out = os.path.join(DATA, "replay_entities_%s.jsonl" % date)
    with open(out, "w", encoding="utf-8") as g:
        for snap in agg.closed:
            g.write(json.dumps(snap, ensure_ascii=False) + "\n")
    print("=== РЕПЛЕЙ %s: принтов %d, серий (n>=%d) %d ==="
          % (date, n_in, EA.MIN_N, len(agg.closed)))
    by = collections.defaultdict(list)
    for r in agg.closed:
        by[r["symbol"]].append(r)
    print("%-14s%6s%7s%10s  %s" % ("TICKER", "серий", "n_max", "qty_sum", "теги"))
    for sym, rs in sorted(by.items(),
                          key=lambda kv: -sum(x["total_qty"] for x in kv[1])):
        tags = collections.Counter(t for r in rs for t in r["tags"])
        print("%-14s%6d%7d%10d  %s" % (sym, len(rs),
              max(r["n"] for r in rs),
              int(sum(r["total_qty"] for r in rs)),
              ",".join("%s:%d" % kv for kv in tags.most_common(3))))
    print("\nТоп-10 серий по обороту:")
    for r in sorted(agg.closed, key=lambda r: -r["total_qty"])[:10]:
        print("%-14s%-5s n=%5d qty=%8d int=%5ss jms=%7.0f теги=%s"
              % (r["symbol"], r["side"], r["n"], int(r["total_qty"]),
                 r["int_s"], r["jms_ms"], ",".join(r["tags"]) or "-"))
    print("\nЗаписано: %s" % out)

if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else time.strftime("%Y-%m-%d"))
