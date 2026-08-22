"""
Приблуда на python — линейка A/B: наш детектор против эталона конкурента.

Линейка для подгонки приблуды. Гонит детектор по ленте за даты эталона и
матчит эталон -> наши сигналы. Ничего не меняет в детекторе, только читает.

Использование (из корня проекта):
    python research/ab_compare.py

Читает:
    data/competitor_history.jsonl  — эталон конкурента
    data/quik_trades.csv           — лента Quik (4 дня: 08-18..08-21)
    ticker_settings.json           — настройки тикеров

Печатает таблицу TP/FN/FP и ДИАГНОСТИКУ первых 5 FN
(почему эталон не сматчился: какие наши сигналы есть по этому
тикеру/стороне, их интервалы и временные окна).

ФИКС МАТЧИНГА (2026-08-22): эталонный "timestamp" — это момент скриншота
конкурента, а не старт серии. Матчим по попаданию эталонного момента
внутрь интервала нашей серии [start, end]. Дата учитывается автоматически.

ФИКС ПАРСИНГА ЭТАЛОНА (2026-08-22): было eval() — падал на JSON null
и молча терял записи без времени. Стало json.loads().
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from core.config import get_detector_configs
from core.ticker_settings import load_settings
from detectors.interval_robot import IntervalRobotDetector

MSK = ZoneInfo("Europe/Moscow")
COMP_PATH = BASE / "data" / "competitor_history.jsonl"
TAPE_PATH = BASE / "data" / "quik_trades.csv"

# Допуск: момент скриншота может быть до этого числа секунд ПОСЛЕ
# последнего удара серии (серия ещё считается активной у конкурента).
REF_AFTER_END_SEC = 600

SIG_RE = re.compile(r"^\[робот-интервал\]\s+(\S+)\s+(buy|sell)\s+qty=(\S+)\s+повторов=(\d+)\s+интервал~([\d.]+)с\s+джиттер=([\d.]+)мс\s+с\s+(\d{2}:\d{2}:\d{2})\s+по\s+(\d{2}:\d{2}:\d{2})")


def load_ref():
    """Эталон конкурента: список словарей. Читаем через json.loads
    (eval падал на null и молча терял записи)."""
    refs = []
    if not COMP_PATH.exists():
        print(f"[ab_compare] Файл эталона не найден: {COMP_PATH}")
        return refs
    with open(COMP_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if isinstance(rec, dict) and "symbol" in rec and "side" in rec:
                    refs.append(rec)
            except Exception:
                continue
    return refs


def parse_signal_line(line):
    """Парсит строку сигнала run_detectors. Возвращает dict или None."""
    m = SIG_RE.match(line.strip())
    if not m:
        return None
    symbol, side, qty_s, reps_s, int_s, jit_s, t_start, t_end = m.groups()
    qty_parts = qty_s.split("-")
    qty_min = int(qty_parts[0])
    qty_max = int(qty_parts[-1]) if len(qty_parts) > 1 else qty_min
    return {
        "symbol": symbol,
        "side": side,
        "qty_min": qty_min,
        "qty_max": qty_max,
        "repeats": int(reps_s),
        "interval": float(int_s),
        "jitter": float(jit_s),
        "t_start": t_start,
        "t_end": t_end,
    }


def signal_to_dict(sig):
    """Конвертирует объект Signal в dict. Добавлены start_ts/end_ts
    (эпоха, секунды) для корректного матчинга по времени."""
    qty_min = min(sig.qty_variants) if sig.qty_variants else 0
    qty_max = max(sig.qty_variants) if sig.qty_variants else 0
    t_start = datetime.fromtimestamp(sig.start_ts, tz=MSK).strftime("%H:%M:%S")
    t_end = datetime.fromtimestamp(sig.end_ts, tz=MSK).strftime("%H:%M:%S")
    return {
        "symbol": sig.symbol,
        "side": sig.side,
        "qty_min": qty_min,
        "qty_max": qty_max,
        "repeats": sig.repeats,
        "interval": sig.interval_avg,
        "jitter": sig.jitter_ms if sig.jitter_ms is not None else 0.0,
        "t_start": t_start,
        "t_end": t_end,
        "start_ts": sig.start_ts,
        "end_ts": sig.end_ts,
    }


def run_detectors_on_day(day_str, settings):
    """Гонит детекторы по ленте за один день. Возвращает список словарей."""
    signals = []
    day_dt = datetime.strptime(day_str, "%Y-%m-%d").date()
    day_detectors = None
    with open(TAPE_PATH, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            parts = line.strip().split(";")
            if len(parts) < 5:
                continue
            sym = parts[0]
            if sym not in settings:
                continue
            try:
                ts_ms = int(float(parts[4]))
                ts = ts_ms / 1000.0
            except (ValueError, TypeError):
                continue
            trade_dt = datetime.fromtimestamp(ts, tz=MSK).date()
            if trade_dt != day_dt:
                continue
            # Детекторы создаются один раз на день
            if day_detectors is None:
                day_detectors = {
                    s: [IntervalRobotDetector(s, cfg)
                        for cfg in get_detector_configs(s, ov.get("min_qty", 1), ov)]
                    for s, ov in settings.items()
                }
            trade = {
                "symbol": sym,
                "qty": int(float(parts[1])),
                "price": float(parts[2]),
                "side": parts[3],
                "timestamp": ts_ms,
            }
            for det in day_detectors.get(sym, []):
                for sig in det.on_trade(trade):
                    signals.append(signal_to_dict(sig))
    # Дожимаем оставшиеся серии
    if day_detectors is not None:
        for dets in day_detectors.values():
            for det in dets:
                for sig in det.flush():
                    signals.append(signal_to_dict(sig))
    return signals


def match_signal_to_ref(ref, signals):
    """Ищет в наших сигналах сигнал, совпадающий с эталоном.
    Возвращает сигнал (словарь) или None."""
    sym = ref.get("symbol")
    side = ref.get("side")
    ref_qty = ref.get("qty_variants")
    ref_int = ref.get("interval_avg")
    ref_ts_str = ref.get("timestamp", "")
    try:
        ref_ts = datetime.fromisoformat(ref_ts_str).timestamp()
    except (ValueError, TypeError):
        ref_ts = None

    for sig in signals:
        if sig["symbol"] != sym or sig["side"] != side:
            continue
        # Совпадение по интервалу: в пределах 30% или кратно
        if ref_int and sig["interval"]:
            ratio = sig["interval"] / ref_int if ref_int else 999
            if not (0.7 <= ratio <= 1.3):
                continue
        # Совпадение по qty: если в эталоне есть qty_variants
        if ref_qty and sig["qty_min"] is not None:
            if not any(sig["qty_min"] <= q <= sig["qty_max"] for q in ref_qty):
                continue
        # Совпадение по времени: эталонный момент (скриншот конкурента)
        # должен попадать ВНУТРЬ интервала нашей серии [start, end]
        # (серия уже началась и ещё активна). Дата учитывается сама.
        if ref_ts is not None:
            sig_start = sig.get("start_ts")
            sig_end = sig.get("end_ts")
            if sig_start is None or sig_end is None:
                continue
            if not (sig_start <= ref_ts <= sig_end + REF_AFTER_END_SEC):
                continue
        return sig
    return None


def diagnose_fn(ref, signals, top_n=5):
    """Диагностика: почему эталон не сматчился. Возвращает список строк."""
    lines = []
    sym = ref.get("symbol")
    side = ref.get("side")
    ref_int = ref.get("interval_avg")
    ref_qty = ref.get("qty_variants")
    ref_ts_str = ref.get("timestamp", "")
    try:
        ref_ts = datetime.fromisoformat(ref_ts_str).timestamp()
        ref_time_str = datetime.fromtimestamp(ref_ts, tz=MSK).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        ref_ts = None
        ref_time_str = "?"
    lines.append(f"{sym} {side}  ref_int={ref_int} ref_qty={ref_qty}  эталон.момент={ref_time_str} MSK")
    candidates = [s for s in signals if s["symbol"] == sym and s["side"] == side]
    candidates.sort(key=lambda s: -s["repeats"])
    if not candidates:
        lines.append("   -> НЕТ ни одного нашего сигнала с этим тикером+стороной")
        return lines
    lines.append(f"   -> Наш сигналов {len(candidates)}, топ-{min(top_n, len(candidates))} по повторам:")
    for i, s in enumerate(candidates[:top_n]):
        st = datetime.fromtimestamp(s["start_ts"], tz=MSK).strftime("%H:%M:%S")
        en = datetime.fromtimestamp(s["end_ts"], tz=MSK).strftime("%H:%M:%S")
        ratio = s["interval"] / ref_int if (ref_int and s["interval"]) else None
        ratio_str = f"{ratio:.2f}" if ratio else "?"
        lines.append(f"      {i+1}. qty={s['qty_min']}-{s['qty_max']} повт={s['repeats']} "
                     f"int={s['interval']:.1f} (ratio={ratio_str}) [{st} - {en}]")
    return lines


def main():
    settings = load_settings()
    refs = load_ref()
    print(f"[ab_compare] Эталон: {len(refs)} записей")
    print(f"[ab_compare] Активных тикеров в настройках: {len(settings)}")
    print()

    # Собираем уникальные даты из эталона
    dates = set()
    for r in refs:
        ts = r.get("timestamp", "")
        try:
            d = datetime.fromisoformat(ts).date().isoformat()
            dates.add(d)
        except (ValueError, TypeError):
            continue
    dates = sorted(dates)
    print(f"[ab_compare] Даты эталона: {dates}")

    tp_list, fn_list = [], []
    all_signals = []
    for day in dates:
        print(f"[ab_compare] Гоню детекторы за {day} ...")
        day_signals = run_detectors_on_day(day, settings)
        all_signals.extend(day_signals)
        print(f"  -> {len(day_signals)} сигналов")

    # Матчим эталон -> наши сигналы
    for ref in refs:
        sig = match_signal_to_ref(ref, all_signals)
        if sig:
            tp_list.append((ref, sig))
        else:
            fn_list.append(ref)

    # FP: наши сигналы без пары в эталоне
    matched_signals = set()
    for ref, sig in tp_list:
        matched_signals.add(id(sig))
    fp_list = [sig for sig in all_signals if id(sig) not in matched_signals]

    print()
    print("=" * 70)
    print("A/B СРАВНЕНИЕ: наш детектор против эталона конкурента")
    print("=" * 70)
    print(f"Эталон: {len(refs)} записей")
    print(f"TP (нашли):      {len(tp_list)}")
    print(f"FN (пропустили): {len(fn_list)}")
    print(f"FP? (наши без пары): {len(fp_list)}")
    print()

    print("--- TP (эталон найден) ---")
    for ref, sig in tp_list:
        ref_int = ref.get("interval_avg", "?")
        ref_qty = ref.get("qty_variants", "?")
        print(f"  {ref['symbol']:6} {ref['side']:4} ref_int={ref_int:>6} "
              f"ref_qty={str(ref_qty):>12} | наш int={sig['interval']:.1f} "
              f"повт={sig['repeats']} jit={sig['jitter']:.0f}мс")

    print()
    print("--- FN (эталон пропущен) ---")
    for ref in fn_list:
        ref_int = ref.get("interval_avg", "?")
        ref_qty = ref.get("qty_variants", "?")
        print(f"  {ref['symbol']:6} {ref['side']:4} ref_int={ref_int:>6} "
              f"ref_qty={str(ref_qty):>12}")

    print()
    print("--- ДИАГНОСТИКА первых 5 FN (почему не сматчились) ---")
    for ref in fn_list[:5]:
        for line in diagnose_fn(ref, all_signals):
            print(f"  {line}")
        print()

    print("--- FP? (наши сигналы без пары в эталоне, первые 30) ---")
    fp_sorted = sorted(fp_list, key=lambda s: -s["repeats"])
    for sig in fp_sorted[:30]:
        print(f"  {sig['symbol']:6} {sig['side']:4} qty={sig['qty_min']}-{sig['qty_max']} "
              f"повт={sig['repeats']:>3} int={sig['interval']:.1f} jit={sig['jitter']:.0f}мс")

    print()
    print("[ab_compare] Готово. Сохраните вывод для сравнения ДО/ПОСЛЕ правок.")


if __name__ == "__main__":
    main()