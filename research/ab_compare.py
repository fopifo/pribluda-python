"""
Приблуда на python — линейка A/B: наш детектор против эталона конкурента.
Линейка для подгонки приблуды. Гонит детектор по ленте за даты эталона и
матчит эталон -> наши сигналы. Ничего не меняет в детекторе, только читает.

Использование (из корня проекта):
    python research/ab_compare.py              # использует кэш для старых дат
    python research/ab_compare.py --recalc     # полный пересчёт всех дат

Кэш по датам (output/ab_compare_cache_<дата>.json):
- результат прогона по дате сохраняется и переиспользуется
- старые даты (20.08, 21.08) берутся из кэша мгновенно
- прогоняется заново только новая дата (например 24.08)
- --recalc — игнорировать кэш, пересчитать всё

Читает:
    data/competitor_history.jsonl  — эталон конкурента
    data/quik_trades.csv           — лента Quik
    ticker_settings.json           — настройки тикеров

Печатает таблицу TP/FN/FP и ДИАГНОСТИКУ первых 5 FN.

ФИКС МЕТРИКИ FP (2026-08-25, по ревью): FP считается ТОЛЬКО если в окне
жизни серии есть момент скриншота конкурента (конкурент смотрел на этот
тикер и не увидел наш робот / увидел другого). Если скриншотов в окне
нет — сигнал UNVERIFIED (не с чем сравнивать) и в FP не попадает.
Без этого FP был мусором: считал ВСЕ наши сигналы без пары за все дни.

ПРОГРЕСС-БАР (2026-08-26): визуальная шкала с процентами, скоростью
и ETA для всех длинных операций (прогон по ленте, матчинг).

UTF-8 FIX (2026-08-26): принудительный UTF-8 для stdout — иначе
PowerShell при перенаправлении ">" использует cp1251 и символы
прогресс-бара (█░) падают с UnicodeEncodeError.
"""
import bisect
import json
import re
import sys
import time
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
CACHE_DIR = BASE / "output"

# Допуск: момент скриншота может быть до этого числа секунд ПОСЛЕ
# последнего удара серии (серия ещё считается активной у конкурента).
REF_AFTER_END_SEC = 600

SIG_RE = re.compile(
    r"^\[робот-интервал\]\s+(\S+)\s+(buy|sell)\s+qty=(\S+)\s+повторов=(\d+)\s+"
    r"интервал~([\d.]+)с\s+джиттер=([\d.]+)мс\s+с\s+(\d{2}:\d{2}:\d{2})\s+по\s+(\d{2}:\d{2}:\d{2})"
)


# ---------- ПРОГРЕСС-БАР (2026-08-26) ----------
def _format_eta(seconds):
    """Форматирует секунды в человекочитаемый вид."""
    if seconds < 60:
        return f"{seconds:.0f}с"
    if seconds < 3600:
        return f"{seconds/60:.1f}мин"
    return f"{seconds/3600:.1f}ч"


def _progress_bar(current, total, width=30):
    """ASCII прогресс-бар: [██████░░░░] 60.0%."""
    if total <= 0:
        return f"[{'?' * width}]"
    pct = current / total
    filled = int(width * pct)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {pct*100:5.1f}%"


def _print_progress(prefix, current, total, start_time, extra=""):
    """Печатает строку прогресса с процентами, скоростью и ETA."""
    elapsed = time.time() - start_time
    speed = current / max(elapsed, 0.001)
    remaining = (total - current) / max(speed, 1) if total > 0 else 0
    bar = _progress_bar(current, total)
    speed_str = f"{speed/1000:.0f}K/с" if speed > 1000 else f"{speed:.0f}/с"
    print(
        f"\r{prefix} {bar} {current:,}/{total:,} | {speed_str} | "
        f"ETA {_format_eta(remaining)}{extra}",
        end="", flush=True
    )


def count_lines(path):
    """Подсчёт строк в файле (один проход)."""
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return sum(1 for _ in f)


# ---------- КЭШ ПО ДАТАМ ----------
def cache_path(day_str):
    return CACHE_DIR / f"ab_compare_cache_{day_str}.json"


def load_cache(day_str):
    """Читает кэш по дате. Возвращает список сигналов или None."""
    p = cache_path(day_str)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return None


def save_cache(day_str, signals):
    """Сохраняет список сигналов по дате в кэш."""
    CACHE_DIR.mkdir(exist_ok=True)
    cache_path(day_str).write_text(
        json.dumps(signals, ensure_ascii=False), encoding="utf-8"
    )


# ---------- ЭТАЛОН ----------
def load_ref():
    """Эталон конкурента: список словарей. Читаем через json.loads."""
    refs = []
    if not COMP_PATH.exists():
        print(f"[ab_compare] Файл эталона не найден: {COMP_PATH}", flush=True)
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


def build_shots_by_date(refs):
    """Моменты скриншотов конкурента по датам (timestamp эталона = момент
    скриншота). Несколько записей с одним timestamp = один скриншот."""
    shots = {}
    for r in refs:
        ts_str = r.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str).timestamp()
        except (ValueError, TypeError):
            continue
        day = datetime.fromtimestamp(ts, tz=MSK).date().isoformat()
        shots.setdefault(day, set()).add(ts)
    return {day: sorted(s) for day, s in shots.items()}


def has_screenshot_in_window(sig, shots_by_date):
    """Есть ли момент скриншота конкурента ВНУТРИ жизни нашей серии
    [start, end + REF_AFTER_END_SEC]. Если да — конкурент смотрел на этот
    тикер и не увидел наш робот (или увидел другого) => настоящий FP.
    Если скриншотов в окне нет — сигнал UNVERIFIED (не с чем сравнивать)."""
    start = sig.get("start_ts")
    end = sig.get("end_ts")
    if start is None or end is None:
        return False
    day = datetime.fromtimestamp(start, tz=MSK).date().isoformat()
    shots = shots_by_date.get(day)
    if not shots:
        return False
    lo = start
    hi = end + REF_AFTER_END_SEC
    i = bisect.bisect_left(shots, lo)
    return i < len(shots) and shots[i] <= hi


def parse_signal_line(line):
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
    """Signal -> dict (сериализуемо, сохраняется в кэш)."""
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


# ---------- ПРОГОН ПО ДНЮ (С ПРОГРЕСС-БАРОМ) ----------
def run_detectors_on_day(day_str, settings, total_lines):
    """Гонит детекторы по ленте за один день. Возвращает список dict."""
    signals = []
    day_dt = datetime.strptime(day_str, "%Y-%m-%d").date()
    day_detectors = None

    lines_total = 0
    fed = 0
    skipped_date = 0
    skipped_sym = 0
    skipped_qty = 0

    print(f"[ab_compare]   читаю ленту за {day_str}...", flush=True)
    start_time = time.time()
    last_print_time = start_time
    
    with open(TAPE_PATH, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            lines_total += 1

            # ПРОГРЕСС-БАР каждые 100K строк или каждые 0.5с
            current_time = time.time()
            if lines_total % 100_000 == 0 or current_time - last_print_time >= 0.5:
                _print_progress(
                    f"[ab_compare] {day_str}",
                    lines_total, total_lines, start_time,
                    f" | скормлено={fed:,} сигн={len(signals)}"
                )
                last_print_time = current_time

            parts = line.strip().split(";")
            if len(parts) < 5:
                continue
            sym = parts[0]
            if sym not in settings:
                skipped_sym += 1
                continue
            try:
                ts_ms = int(float(parts[4]))
                ts = ts_ms / 1000.0
            except (ValueError, TypeError):
                continue
            trade_dt = datetime.fromtimestamp(ts, tz=MSK).date()
            if trade_dt != day_dt:
                skipped_date += 1
                continue

            # Детекторы создаются один раз на день
            if day_detectors is None:
                day_detectors = {
                    s: [
                        IntervalRobotDetector(s, cfg)
                        for cfg in get_detector_configs(s, ov.get("min_qty", 1), ov)
                    ]
                    for s, ov in settings.items()
                }

            try:
                qty = int(float(parts[1]))
                price = float(parts[2])
            except (ValueError, IndexError):
                skipped_qty += 1
                continue

            ov = settings.get(sym, {})
            if qty < ov.get("min_qty", 1):
                skipped_qty += 1
                continue

            trade = {
                "symbol": sym,
                "qty": qty,
                "price": price,
                "side": parts[3],
                "timestamp": ts_ms,
            }
            for det in day_detectors.get(sym, []):
                for sig in det.on_trade(trade):
                    signals.append(signal_to_dict(sig))
            fed += 1

    # Финальная строка прогресса
    print()
    print(
        f"[ab_compare]   {day_str} ГОТОВО: строк={lines_total:,}, "
        f"скормлено={fed:,}, сигналов={len(signals)}",
        flush=True,
    )
    return signals


# ---------- МАТЧИНГ ----------
def match_signal_to_ref(ref, signals):
    """Ищет в наших сигналах сигнал, совпадающий с эталоном."""
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
        if ref_int and sig["interval"]:
            ratio = sig["interval"] / ref_int if ref_int else 999
            if not (0.7 <= ratio <= 1.3):
                continue
        if ref_qty and sig["qty_min"] is not None:
            if not any(sig["qty_min"] <= q <= sig["qty_max"] for q in ref_qty):
                continue
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
    """Диагностика: почему эталон не сматчился."""
    lines = []
    sym = ref.get("symbol")
    side = ref.get("side")
    ref_int = ref.get("interval_avg")
    ref_qty = ref.get("qty_variants")
    ref_ts_str = ref.get("timestamp", "")
    try:
        ref_ts = datetime.fromisoformat(ref_ts_str).timestamp()
        ref_time_str = datetime.fromtimestamp(ref_ts, tz=MSK).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except (ValueError, TypeError):
        ref_ts = None
        ref_time_str = "?"
    lines.append(
        f"{sym} {side}  ref_int={ref_int} ref_qty={ref_qty}  "
        f"эталон.момент={ref_time_str} MSK"
    )
    candidates = [s for s in signals if s["symbol"] == sym and s["side"] == side]
    candidates.sort(key=lambda s: -s["repeats"])
    if not candidates:
        lines.append("   -> НЕТ ни одного нашего сигнала с этим тикером+стороной")
        return lines
    lines.append(
        f"   -> Наш сигналов {len(candidates)}, "
        f"топ-{min(top_n, len(candidates))} по повторам:"
    )
    for i, s in enumerate(candidates[:top_n]):
        st = datetime.fromtimestamp(s["start_ts"], tz=MSK).strftime("%H:%M:%S")
        en = datetime.fromtimestamp(s["end_ts"], tz=MSK).strftime("%H:%M:%S")
        ratio = s["interval"] / ref_int if (ref_int and s["interval"]) else None
        ratio_str = f"{ratio:.2f}" if ratio else "?"
        lines.append(
            f"      {i+1}. qty={s['qty_min']}-{s['qty_max']} повт={s['repeats']} "
            f"int={s['interval']:.1f} (ratio={ratio_str}) [{st} - {en}]"
        )
    return lines


# ---------- MAIN ----------
def main():
    # UTF-8 FIX (2026-08-26): принудительный UTF-8 для stdout.
    # Без этого PowerShell при перенаправлении ">" использует cp1251
    # и символы прогресс-бара (█░) падают с UnicodeEncodeError.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    recalc_all = "--recalc" in sys.argv

    settings = load_settings()
    refs = load_ref()
    print(f"[ab_compare] Эталон: {len(refs)} записей", flush=True)
    print(
        f"[ab_compare] Активных тикеров в настройках: {len(settings)}", flush=True
    )
    print(f"[ab_compare] Режим: {'полный пересчёт' if recalc_all else 'кэш+новые даты'}", flush=True)
    print(flush=True)

    # Уникальные даты эталона
    dates = set()
    for r in refs:
        ts = r.get("timestamp", "")
        try:
            d = datetime.fromisoformat(ts).date().isoformat()
            dates.add(d)
        except (ValueError, TypeError):
            continue
    dates = sorted(dates)
    print(f"[ab_compare] Даты эталона: {dates}", flush=True)

    if not dates:
        print("[ab_compare] ВНИМАНИЕ: ни одной даты в эталоне — прогон не имеет смысла.", flush=True)
        return

    # Моменты скриншотов конкурента по датам (для классификации FP)
    shots_by_date = build_shots_by_date(refs)
    for day in dates:
        print(
            f"[ab_compare]   {day}: скриншотов={len(shots_by_date.get(day, []))}",
            flush=True,
        )

    # Подсчёт общего количества строк в ленте (для прогресс-бара)
    print("[ab_compare] Подсчёт строк в ленте...", flush=True)
    total_lines = count_lines(TAPE_PATH)
    print(f"[ab_compare] Всего строк в ленте: {total_lines:,}", flush=True)

    # Прогон по датам с кэшем
    all_signals = []
    cached_count = 0
    for day in dates:
        cached = None if recalc_all else load_cache(day)
        if cached is not None:
            print(
                f"[ab_compare] === {day}: из кэша ({len(cached)} сигналов) ===",
                flush=True,
            )
            all_signals.extend(cached)
            cached_count += 1
        else:
            print(f"[ab_compare] === ПРОГОН за {day} ===", flush=True)
            day_signals = run_detectors_on_day(day, settings, total_lines)
            all_signals.extend(day_signals)
            save_cache(day, day_signals)
            print(
                f"[ab_compare] === {day}: кэш сохранён ({len(day_signals)} сигналов) ===",
                flush=True,
            )

    print(
        f"[ab_compare] ИТОГО: {len(all_signals)} сигналов "
        f"(кэш использован для {cached_count} из {len(dates)} дат)",
        flush=True,
    )

    # Матчинг с прогресс-баром
    tp_list, fn_list = [], []
    print(f"[ab_compare] Матчинг {len(refs)} эталонных записей...", flush=True)
    start_time = time.time()
    last_print_time = start_time
    for i, ref in enumerate(refs, 1):
        current_time = time.time()
        if i % 10 == 0 or current_time - last_print_time >= 0.5:
            _print_progress("[ab_compare] Матчинг", i, len(refs), start_time)
            last_print_time = current_time
        sig = match_signal_to_ref(ref, all_signals)
        if sig:
            tp_list.append((ref, sig))
        else:
            fn_list.append(ref)
    print()  # перевод строки после прогресс-бара

    # FP (реальные) vs UNVERIFIED: наши сигналы без пары в эталоне.
    # Реальный FP = в окне жизни серии ЕСТЬ момент скриншота конкурента
    # (конкурент смотрел на тикер и не увидел наш робот / увидел другого).
    # UNVERIFIED = скриншотов в окне нет, сравнивать не с чем.
    matched_signals = set()
    for ref, sig in tp_list:
        matched_signals.add(id(sig))
    fp_list, unverified_list = [], []
    for sig in all_signals:
        if id(sig) in matched_signals:
            continue
        if has_screenshot_in_window(sig, shots_by_date):
            fp_list.append(sig)
        else:
            unverified_list.append(sig)

    print(flush=True)
    print("=" * 70)
    print("A/B СРАВНЕНИЕ: наш детектор против эталона конкурента")
    print("=" * 70)
    print(f"Эталон: {len(refs)} записей")
    print(f"TP (нашли):      {len(tp_list)}")
    print(f"FN (пропустили): {len(fn_list)}")
    print(f"FP (реальные, скриншот в окне): {len(fp_list)}")
    print(f"UNVERIFIED (скриншота в окне нет): {len(unverified_list)}")
    print(flush=True)

    print("--- TP (эталон найден) ---")
    for ref, sig in tp_list:
        ref_int = ref.get("interval_avg", "?")
        ref_qty = ref.get("qty_variants", "?")
        print(
            f"  {ref['symbol']:6} {ref['side']:4} ref_int={ref_int:>6} "
            f"ref_qty={str(ref_qty):>12} | наш int={sig['interval']:.1f} "
            f"повт={sig['repeats']} jit={sig['jitter']:.0f}мс"
        )
    print(flush=True)

    print("--- FN (эталон пропущен) ---")
    for ref in fn_list:
        ref_int = ref.get("interval_avg", "?")
        ref_qty = ref.get("qty_variants", "?")
        print(
            f"  {ref['symbol']:6} {ref['side']:4} ref_int={ref_int:>6} "
            f"ref_qty={str(ref_qty):>12}"
        )
    print(flush=True)

    print("--- ДИАГНОСТИКА первых 5 FN (почему не сматчились) ---")
    for ref in fn_list[:5]:
        for line in diagnose_fn(ref, all_signals):
            print(f"  {line}")
    print(flush=True)

    print("--- FP (реальные, первые 30) ---")
    fp_sorted = sorted(fp_list, key=lambda s: -s["repeats"])
    for sig in fp_sorted[:30]:
        print(
            f"  {sig['symbol']:6} {sig['side']:4} qty={sig['qty_min']}-{sig['qty_max']} "
            f"повт={sig['repeats']:>3} int={sig['interval']:.1f} jit={sig['jitter']:.0f}мс"
        )
    print(flush=True)

    print("[ab_compare] Готово.", flush=True)


if __name__ == "__main__":
    main()