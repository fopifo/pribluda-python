"""
pipeline_validate.py
====================
Единый пайплайн валидации детектора против эталонов (TW + Aniscan).
Автоматически читает активные тикеры из ticker_settings.json (исключая шумные).
Применяет глобальные пороги min_interval и min_qty ко всем сигналам.
Автоматически классифицирует ложные срабатывания (FP).
НЕ содержит логики торговли. Только анализ.
"""
import sys
import json
import time
import math
import argparse
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    from ai_solution_v1.detect.interval import IntervalDetector, parse_trade
except ImportError:
    print("[ОШИБКА] Не удалось импортировать IntervalDetector. Проверьте наличие ai_solution_v1/detect/interval.py")
    sys.exit(1)

DATA_DIR = BASE_DIR / "data"
SETTINGS_PATH = BASE_DIR / "ticker_settings.json"

EXCLUDED_TICKERS = {"VTBR", "SBER", "SBERP"}

# Глобальные пороги (применяются ко всем сигналам, даже если тикер не распознан)
GLOBAL_MIN_INTERVAL = 5.0  # секунд
GLOBAL_MIN_QTY = 2         # лотов

class Progress:
    def __init__(self, total, label):
        self.total = max(int(total), 1)
        self.label = label
        self.done = 0
        self.t0 = time.time()
        self.last = 0.0
        self.lp = -1
        self.tty = sys.stderr.isatty()

    def _line(self):
        pct = min(self.done * 100 // self.total, 100)
        dt = max(time.time() - self.t0, 1e-9)
        sp = self.done / dt
        eta = (self.total - self.done) / max(sp, 1)
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        return f"[{self.label}] [{bar}] {pct:3d}% | {self.done}/{self.total} | {sp:.0f}/s | ETA {eta:.0f}s"

    def update(self, n=1):
        self.done += n
        pct = min(self.done * 100 // self.total, 100)
        now = time.time()
        if self.tty:
            if now - self.last < 0.1 and self.done < self.total:
                return
            self.last = now
            sys.stderr.write("\r" + self._line())
            sys.stderr.flush()
        else:
            if pct // 10 != self.lp // 10:
                self.lp = pct
                print(self._line(), file=sys.stderr, flush=True)

    def close(self):
        if self.tty:
            sys.stderr.write("\r" + self._line() + "\n")
        else:
            print(self._line(), file=sys.stderr, flush=True)


def load_settings():
    settings = {}
    if not SETTINGS_PATH.exists():
        return settings
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        for ticker, cfg in data.items():
            if isinstance(cfg, dict):
                settings[ticker.upper()] = {
                    "min_interval": float(cfg.get("min_interval", GLOBAL_MIN_INTERVAL)),
                    "min_qty": int(cfg.get("min_qty", GLOBAL_MIN_QTY)),
                }
    except Exception as e:
        print(f"[ВНИМАНИЕ] Ошибка чтения {SETTINGS_PATH}: {e}")
    return settings


def get_target_tickers(settings):
    active = set()
    for ticker, cfg in settings.items():
        if ticker not in EXCLUDED_TICKERS:
            active.add(ticker)
    return active


def load_etalons(date_str, target_tickers):
    etalons = set()
    sources = [
        DATA_DIR / f"tw_robots_{date_str}.jsonl",
        DATA_DIR / f"aniscan_robots_{date_str}.jsonl"
    ]
    for src in sources:
        if not src.exists():
            continue
        with open(src, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    robots = []
                    if isinstance(data, dict) and "payload" in data and "robots" in data["payload"]:
                        robots = data["payload"]["robots"]
                    elif isinstance(data, list):
                        robots = data
                    elif isinstance(data, dict) and "ticker" in data:
                        robots = [data]
                    for r in robots:
                        ticker = (r.get("ticker") or r.get("symbol") or "").upper()
                        if not ticker or (target_tickers and ticker not in target_tickers):
                            continue
                        side = "buy" if str(r.get("isBuy", r.get("side", "buy"))).lower() in ["true", "buy", "1"] else "sell"
                        interval_ms = float(r.get("interval", 0))
                        int_sec = round(interval_ms / 1000.0) if interval_ms > 1000 else round(interval_ms)
                        etalons.add((ticker, side, int_sec))
                except json.JSONDecodeError:
                    continue
    return etalons


def classify_fp(signal):
    hits = getattr(signal, "hits", 0)
    regularity = getattr(signal, "regularity", 1.0)
    qty_variants = getattr(signal, "qty_variants", [])
    if hits < 5:
        return "SHORT"
    if len(qty_variants) > 1:
        min_q, max_q = min(qty_variants), max(qty_variants)
        if min_q > 0 and (max_q / min_q) > 2.0:
            return "QTY_CHAOS"
    if regularity < 0.7:
        return "DRIFT"
    return "NOISE"


def passes_global_filters(sig):
    """Жесткие глобальные пороги для всех сигналов."""
    interval_sec = sig.base_interval_ms / 1000.0
    if interval_sec < GLOBAL_MIN_INTERVAL:
        return False
    qty_variants = getattr(sig, "qty_variants", [])
    if qty_variants and min(qty_variants) < GLOBAL_MIN_QTY:
        return False
    return True


def passes_settings(sig, settings):
    """Дополнительная проверка по настройкам конкретного тикера."""
    ticker = sig.symbol.upper() if sig.symbol else ""
    if not ticker:
        return True  # тикер не распознан — полагаемся на глобальный фильтр
    cfg = settings.get(ticker)
    if cfg is None:
        return True
    interval_sec = sig.base_interval_ms / 1000.0
    if interval_sec < cfg["min_interval"]:
        return False
    qty_variants = getattr(sig, "qty_variants", [])
    if qty_variants and min(qty_variants) < cfg["min_qty"]:
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="Валидация детектора против эталонов")
    parser.add_argument("--date", default="2026-11-22", help="Дата для анализа (YYYY-MM-DD)")
    parser.add_argument("--tape", default="", help="Путь к файлу ленты. По умолчанию: data/live_trades_{date}.jsonl")
    args = parser.parse_args()

    settings = load_settings()
    target_tickers = get_target_tickers(settings)
    date_str = args.date

    tape_path = Path(args.tape) if args.tape else (DATA_DIR / f"live_trades_{date_str}.jsonl")
    if not tape_path.exists():
        tape_path = DATA_DIR / "replay_alor_history.jsonl"
        if not tape_path.exists():
            print(f"[ОШИБКА] Не найдена лента: {tape_path}")
            sys.exit(1)

    print("=" * 70)
    print(f" Пайплайн валидации | Дата: {date_str}")
    print(f" Лента: {tape_path.name}")
    print(f" Целевых тикеров: {len(target_tickers)}")
    print(f" Глобальные пороги: min_interval={GLOBAL_MIN_INTERVAL}s, min_qty={GLOBAL_MIN_QTY}")
    print("=" * 70)

    print("\n[1/3] Загрузка эталонов (TW + Aniscan)...")
    etalons = load_etalons(date_str, target_tickers)
    print(f"  Найдено уникальных паттернов в эталонах: {len(etalons)}")

    print(f"\n[2/3] Прогон детектора через ленту...")
    detector = IntervalDetector()
    tape_size = tape_path.stat().st_size
    tape_read = 0

    with open(tape_path, "r", encoding="utf-8") as f:
        pr = Progress(tape_size, "tape")
        for line in f:
            tape_read += len(line.encode('utf-8'))
            pr.update(len(line.encode('utf-8')))
            try:
                raw = json.loads(line)
                trade = parse_trade(raw)
                if trade:
                    if not target_tickers or trade.symbol.upper() in target_tickers:
                        detector.on_trade(trade)
            except Exception:
                continue
        pr.close()

    all_signals = detector.reported
    print(f"  Детектор сырых сигналов: {len(all_signals)}")

    # Отладка: покажем тикеры сырых сигналов
    ticker_counts = defaultdict(int)
    for s in all_signals:
        ticker_counts[s.symbol if s.symbol else "(пусто)"] += 1
    print(f"  Распределение по тикерам: {dict(ticker_counts)}")

    # Применяем глобальные пороги
    after_global = [s for s in all_signals if passes_global_filters(s)]
    print(f"  После глобального фильтра (interval>={GLOBAL_MIN_INTERVAL}s, qty>={GLOBAL_MIN_QTY}): {len(after_global)}")

    # Применяем пороги из настроек
    filtered_signals = [s for s in after_global if passes_settings(s, settings)]
    print(f"  После фильтра по настройкам тикеров: {len(filtered_signals)}")

    print("\n[3/3] Оценка и классификация...")
    tp = 0
    fp_counts = defaultdict(int)
    fp_details = []
    matched_etalons = set()

    for sig in filtered_signals:
        ticker = sig.symbol.upper() if sig.symbol else "(неизвестно)"
        side = "buy" if sig.side == 1 else "sell"
        int_sec = round(sig.base_interval_ms / 1000.0)

        key = (ticker, side, int_sec)
        if key in etalons:
            tp += 1
            matched_etalons.add(key)
        else:
            fp_class = classify_fp(sig)
            fp_counts[fp_class] += 1
            if len(fp_details) < 10:
                fp_details.append({
                    "ticker": ticker, "side": side, "int": int_sec,
                    "hits": sig.hits, "reg": getattr(sig, "regularity", 0.0),
                    "qty": getattr(sig, "qty_variants", []), "reason": fp_class
                })

    fn = len(etalons) - len(matched_etalons)
    total_detected = tp + sum(fp_counts.values())
    precision = (tp / total_detected * 100) if total_detected > 0 else 0.0
    recall = (len(matched_etalons) / len(etalons) * 100) if len(etalons) > 0 else 0.0

    print("\n" + "=" * 70)
    print(" ИТОГОВЫЕ МЕТРИКИ")
    print("=" * 70)
    print(f" True Positives (TP) : {tp}")
    print(f" False Negatives (FN): {fn} (пропущено эталоном)")
    print(f" False Positives (FP): {sum(fp_counts.values())}")
    print(f" Precision           : {precision:.1f}%")
    print(f" Recall              : {recall:.1f}%")

    print("\n" + "-" * 70)
    print(" КЛАССИФИКАЦИЯ FP (почему детектор ошибся):")
    print("-" * 70)
    for reason, count in sorted(fp_counts.items(), key=lambda x: -x[1]):
        print(f"  {reason:<12}: {count}")

    print("\n" + "-" * 70)
    print(" ТОП-10 FP ДЛЯ АНАЛИЗА:")
    print("-" * 70)
    for d in fp_details:
        print(f"  {d['ticker']:<10} {d['side']:<4} int={d['int']:3d}s | hits={d['hits']:2d} reg={d['reg']:.2f} qty={d['qty']} | [{d['reason']}]")

    print("\n" + "=" * 70)
    print(" ВАЛИДАЦИЯ ЗАВЕРШЕНА")
    print("=" * 70)

if __name__ == "__main__":
    main()