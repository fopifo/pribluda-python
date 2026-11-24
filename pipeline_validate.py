import sys, json, time, argparse
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    from ai_solution_v1.detect.interval import IntervalDetector, parse_trade
except ImportError:
    print("[ОШИБКА] Не удалось импортировать IntervalDetector.")
    sys.exit(1)

DATA_DIR = BASE_DIR / "data"
SETTINGS_PATH = BASE_DIR / "ticker_settings.json"
EXCLUDED_TICKERS = {"VTBR", "SBER", "SBERP"}
GLOBAL_MIN_INTERVAL = 5.0
GLOBAL_MIN_QTY = 1
INTERVAL_TOLERANCE_SEC = 1.0

class Progress:
    def __init__(self, total, label):
        self.total, self.label, self.done = max(int(total), 1), label, 0
        self.t0, self.last, self.lp, self.tty = time.time(), 0.0, -1, sys.stderr.isatty()
    def _line(self):
        pct = min(self.done * 100 // self.total, 100)
        dt = max(time.time() - self.t0, 1e-9)
        return f"[{self.label}] [{'#'*(pct//5)+'.'*(20-pct//5)}] {pct:3d}% | {self.done}/{self.total} | {self.done/dt:.0f}/s | ETA {(self.total-self.done)/max(self.done/dt,1):.0f}s"
    def update(self, n=1):
        self.done += n
        pct = min(self.done * 100 // self.total, 100)
        now = time.time()
        if self.tty:
            if now - self.last < 0.1 and self.done < self.total: return
            self.last = now
            sys.stderr.write("\r" + self._line()); sys.stderr.flush()
        else:
            if pct // 10 != self.lp // 10:
                self.lp = pct
                print(self._line(), file=sys.stderr, flush=True)
    def close(self):
        if self.tty: sys.stderr.write("\r" + self._line() + "\n")
        else: print(self._line(), file=sys.stderr, flush=True)

def load_settings():
    settings = {}
    if not SETTINGS_PATH.exists(): return settings
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8-sig") as f: data = json.load(f)
        for ticker, cfg in data.items():
            if isinstance(cfg, dict):
                settings[ticker.upper()] = {"min_interval": float(cfg.get("min_interval", GLOBAL_MIN_INTERVAL)), "min_qty": int(cfg.get("min_qty", GLOBAL_MIN_QTY))}
    except Exception as e: print(f"[ВНИМАНИЕ] Ошибка чтения {SETTINGS_PATH}: {e}")
    return settings

def get_target_tickers(settings):
    return {ticker for ticker, cfg in settings.items() if ticker not in EXCLUDED_TICKERS}

def load_etalons(date_str, target_tickers):
    etalons = defaultdict(set)
    for src in [DATA_DIR / f"tw_robots_{date_str}.jsonl", DATA_DIR / f"aniscan_robots_{date_str}.jsonl"]:
        if not src.exists(): continue
        with open(src, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip(): continue
                try:
                    data = json.loads(line)
                    robots = data.get("payload", {}).get("robots", []) if isinstance(data, dict) else (data if isinstance(data, list) else ([data] if "ticker" in data else []))
                    for r in robots:
                        ticker = (r.get("ticker") or r.get("symbol") or "").upper()
                        if not ticker or (target_tickers and ticker not in target_tickers): continue
                        side = "buy" if str(r.get("isBuy", r.get("side", "buy"))).lower() in ["true", "buy", "1"] else "sell"
                        etalons[(ticker, side)].add(round(float(r.get("interval", 0)) / 1000.0))
                except json.JSONDecodeError: continue
    return etalons, sum(len(v) for v in etalons.values())

def classify_fp(signal):
    hits, reg, qv = getattr(signal, "hits", 0), getattr(signal, "regularity", 1.0), getattr(signal, "qty_variants", [])
    if hits < 5: return "SHORT"
    if len(qv) > 1 and min(qv) > 0 and (max(qv) / min(qv)) > 2.0: return "QTY_CHAOS"
    if reg < 0.7: return "DRIFT"
    return "NOISE"

def passes_filters(sig, settings):
    ticker = sig.symbol.upper() if sig.symbol else ""
    if not ticker: return True
    cfg = settings.get(ticker)
    if cfg is None: return True
    if (sig.base_interval_ms / 1000.0) < cfg["min_interval"]: return False
    qv = getattr(sig, "qty_variants", [])
    if qv and min(qv) < cfg["min_qty"]: return False
    return True

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-11-23")
    parser.add_argument("--tape", default="")
    args = parser.parse_args()

    settings = load_settings()
    target_tickers = get_target_tickers(settings)
    tape_path = Path(args.tape) if args.tape else (DATA_DIR / f"live_trades_{args.date}.jsonl")
    if not tape_path.exists():
        tape_path = DATA_DIR / "replay_alor_history.jsonl"
        if not tape_path.exists():
            print(f"[ОШИБКА] Не найдена лента: {tape_path}"); sys.exit(1)

    print("="*70 + f"\n Пайплайн валидации | Дата: {args.date}\n Лента: {tape_path.name}\n Целевых тикеров: {len(target_tickers)}\n Глобальные пороги: min_interval={GLOBAL_MIN_INTERVAL}s, min_qty={GLOBAL_MIN_QTY}, tol=+/-{INTERVAL_TOLERANCE_SEC}с\n" + "="*70)

    print("\n[1/3] Загрузка эталонов...")
    etalons, total_unique = load_etalons(args.date, target_tickers)
    print(f"  Найдено уникальных паттернов: {total_unique}")

    print("\n[2/3] Прогон детектора...")
    detectors = {ticker: IntervalDetector(ticker, None) for ticker in target_tickers}
    with open(tape_path, "r", encoding="utf-8") as f:
        pr = Progress(tape_path.stat().st_size, "tape")
        for line in f:
            pr.update(len(line.encode('utf-8')))
            try:
                trade = parse_trade(json.loads(line))
                if trade and trade.symbol.upper() in detectors:
                    detectors[trade.symbol.upper()].on_trade(trade)
            except Exception: continue
        pr.close()

    all_signals = [sig for det in detectors.values() for sig in det.reported]
    print(f"  Сырых сигналов: {len(all_signals)}")
    
    ticker_counts = defaultdict(int)
    for s in all_signals: ticker_counts[s.symbol if s.symbol else "(пусто)"] += 1
    print(f"  Распределение по тикерам: {dict(ticker_counts)}")

    filtered = [s for s in all_signals if (s.base_interval_ms/1000.0 >= GLOBAL_MIN_INTERVAL) and (not getattr(s, "qty_variants", []) or min(getattr(s, "qty_variants", [])) >= GLOBAL_MIN_QTY) and passes_filters(s, settings)]
    print(f"  После фильтров: {len(filtered)}")

    print("\n[3/3] Оценка...")
    fp_counts, fp_details, matched = defaultdict(int), [], defaultdict(set)
    for sig in filtered:
        ticker, side, det_sec = (sig.symbol.upper() if sig.symbol else ""), ("buy" if sig.side == 1 else "sell"), round(sig.base_interval_ms / 1000.0)
        key = (ticker, side)
        is_match = any(abs(det_sec - et_sec) <= max(1.0, et_sec * 0.10) for et_sec in etalons.get(key, []))
        if is_match:
            for et_sec in etalons.get(key, []):
                if abs(det_sec - et_sec) <= max(1.0, et_sec * 0.10):
                    matched[key].add(et_sec)
                    break
        else:
            fp_class = classify_fp(sig)
            fp_counts[fp_class] += 1
            if len(fp_details) < 10:
                fp_details.append({"ticker": ticker, "side": side, "int_sec": det_sec, "hits": sig.hits, "reg": getattr(sig, "regularity", 0.0), "qty": getattr(sig, "qty_variants", []), "reason": fp_class})

    tp = sum(len(v) for v in matched.values())
    fn = total_unique - tp
    fp = sum(fp_counts.values())
    precision = (tp / (tp + fp) * 100) if (tp + fp) > 0 else 0.0
    recall = (tp / total_unique * 100) if total_unique > 0 else 0.0

    print("\n" + "="*70 + "\n ИТОГОВЫЕ МЕТРИКИ\n" + "="*70)
    print(f" Уникальных эталонов   : {total_unique}")
    print(f" True Positives (TP)   : {tp}")
    print(f" False Negatives (FN)  : {fn}")
    print(f" False Positives (FP)  : {fp}")
    print(f" Precision (Точность)  : {precision:.1f}%")
    print(f" Recall (Полнота)      : {recall:.1f}%")
    print("\n" + "-"*70 + "\n КЛАССИФИКАЦИЯ FP:\n" + "-"*70)
    for reason, count in sorted(fp_counts.items(), key=lambda x: -x[1]): print(f"  {reason:<12}: {count}")
    print("\n" + "-"*70 + "\n ТОП-10 FP ДЛЯ АНАЛИЗА:\n" + "-"*70)
    for d in fp_details: print(f"  {d['ticker']:<10} {d['side']:<4} int={d['int_sec']:5.1f}s | hits={d['hits']:2d} reg={d['reg']:.2f} qty={d['qty']} | [{d['reason']}]")
    print("\n" + "="*70 + "\n ВАЛИДАЦИЯ ЗАВЕРШЕНА\n" + "="*70)

if __name__ == "__main__":
    main()