"""
ai_solution_v1/combined.py
==========================
Связыватель: берёт сигналы из ленты (IntervalDetector) и события из стакана (OrderbookDetector)
и выдает "Мега-Сигнал", если они совпали по тикеру и времени.
Пишет результат в result.txt и копирует в буфер обмена.
"""
import json
import time
import sys
import pyperclip
from pathlib import Path
from typing import List, Dict, Any

from ai_solution_v1.detect.interval import IntervalDetector, Signal, parse_trade
from ai_solution_v1.orderbook import OrderbookDetector, SpoofEvent, WallEvent, parse_snapshot, OrderbookConfig


def _progress_bar(label: str, bytes_read: int, total_bytes: int, start_time: float, extra: str = "") -> None:
    if total_bytes <= 0:
        return
    now = time.perf_counter()
    pct = (bytes_read / total_bytes) * 100
    rate = bytes_read / max(now - start_time, 1e-9)
    eta = (total_bytes - bytes_read) / max(rate, 1)

    bar_len = 20
    filled = int(bar_len * bytes_read // total_bytes)
    bar = "#" * filled + "." * (bar_len - filled)

    sys.stdout.write(f"\r[{label}] [{bar}] {pct:5.1f}% | {rate/1024/1024:.1f} MB/s | ETA {eta:.0f}с | {extra}")
    sys.stdout.flush()


def run_combined(
    tape_path: Path,
    ob_path: Path,
    out_path: Path,
    limit_tape: int = 0,
    limit_ob: int = 0,
    window_ms: int = 60000,  # Окно 60 секунд
):
    # 1. Прогон ленты
    print("[combined] Запуск ленточного детектора...")
    det_tape = IntervalDetector()
    tape_size = tape_path.stat().st_size
    tape_read = 0
    start_tape = time.perf_counter()

    with open(tape_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if limit_tape and i >= limit_tape:
                break
            tape_read += len(line.encode('utf-8'))
            try:
                raw = json.loads(line)
                trade = parse_trade(raw)
                if trade:
                    det_tape.on_trade(trade)
            except Exception:
                continue

            if i % 5000 == 0:
                _progress_bar("tape", tape_read, tape_size, start_tape, f"сигналов: {len(det_tape.reported)}")

    _progress_bar("tape", tape_size, tape_size, start_tape)
    print()

    signals = det_tape.reported
    print(f"[combined] Найдено {len(signals)} интервальных роботов в ленте.")

    # Твоя диагностика по тикерам
    tape_tickers = {}
    for s in signals:
        tape_tickers[s.symbol] = tape_tickers.get(s.symbol, 0) + 1

    print(f"  Распределение по тикерам в ленте:")
    for sym, count in sorted(tape_tickers.items(), key=lambda x: -x[1])[:10]:
        print(f"    {sym}: {count} сигналов")

    # 2. Прогон стакана
    print(f"\n[combined] Запуск стаканного детектора...")
    ob_config = OrderbookConfig()
    det_ob = OrderbookDetector(ob_config)
    ob_events = []

    ob_size = ob_path.stat().st_size
    ob_read = 0
    start_ob = time.perf_counter()

    with open(ob_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if limit_ob and i >= limit_ob:
                break
            ob_read += len(line.encode('utf-8'))
            try:
                raw = json.loads(line)
                snap = parse_snapshot(raw)
                if snap:
                    evs = det_ob.on_snapshot(snap)
                    ob_events.extend(evs)
            except Exception:
                continue

            if i % 5000 == 0:
                _progress_bar("ob  ", ob_read, ob_size, start_ob, f"событий: {len(ob_events)}")

    _progress_bar("ob  ", ob_size, ob_size, start_ob)
    print()

    print(f"[combined] Найдено {len(ob_events)} событий в стакане.")

    # 3. Связывание
    print("[combined] Поиск совпадений (Мега-Сигналы)...")
    mega_signals = []

    ob_tickers = set(e.symbol for e in ob_events)
    common_tickers = set(tape_tickers.keys()).intersection(ob_tickers)

    print(f"  Тикеры в ленте: {len(tape_tickers)}, в стакане: {len(ob_tickers)}, общие: {len(common_tickers)}")

    if common_tickers:
        print(f"  Общие тикеры: {', '.join(list(common_tickers)[:10])}")
    else:
        print("  ВНИМАНИЕ: нет общих тикеров! Проверьте, совпадают ли данные.")

    for sig in signals:
        for ev in ob_events:
            if ev.symbol != sig.symbol:
                continue

            sig_end = sig.end_ms
            sig_start = sig.start_ms
            ev_time = ev.ts_disappeared_ms if isinstance(ev, SpoofEvent) else ev.ts_ms

            if (sig_start - window_ms <= ev_time <= sig_end + window_ms):
                mega_signals.append({
                    "symbol": sig.symbol,
                    "side": "buy" if sig.side == 1 else "sell",
                    "tape_start": sig.start_ms,
                    "tape_end": sig.end_ms,
                    "tape_interval_sec": sig.base_interval_ms / 1000,
                    "tape_hits": sig.hits,
                    "ob_type": ev.kind,
                    "ob_price": ev.price,
                    "ob_qty": ev.qty,
                    "ob_time": ev_time,
                    "delta_ms": min(abs(ev_time - sig_start), abs(ev_time - sig_end)),
                })

    # Формирование отчета
    report_lines = []
    report_lines.append("="*80)
    report_lines.append(" МЕГА-СИГНАЛЫ")
    report_lines.append("="*80)

    if not mega_signals:
        report_lines.append("Совпадений не найдено.")
        if common_tickers:
            report_lines.append(f"Но общие тикеры есть ({len(common_tickers)}). Возможно, мигалки не попадают в окно времени.")
        else:
            report_lines.append("Общих тикеров нет. Лента и стакан не пересекаются.")
    else:
        mega_signals.sort(key=lambda x: x["delta_ms"])
        for ms in mega_signals[:20]:
            report_lines.append(f"  {ms['symbol']:<10} {ms['side']:<4} | Лента: {ms['tape_interval_sec']:.1f}с ({ms['tape_hits']} уд.) | Стакан: {ms['ob_type']:<5} {ms['ob_price']} (qty={ms['ob_qty']}) | Δ={ms['delta_ms']}мс")
        report_lines.append(f"\n... всего найдено {len(mega_signals)} мега-сигналов.")

    report_text = "\n".join(report_lines)
    print("\n" + report_text)

    # 4. Запись в файл
    out_path.write_text(report_text, encoding='utf-8')
    print(f"\n[combined] Результат записан в {out_path}")

    # 5. Копирование в буфер
    try:
        pyperclip.copy(report_text)
        print("[combined] Результат скопирован в буфер обмена.")
    except Exception as e:
        print(f"[combined] Не удалось скопировать в буфер: {e}")

    return mega_signals


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Использование: python -m ai_solution_v1.combined <лента.jsonl> <стакан.jsonl> <result.txt>")
        sys.exit(1)

    run_combined(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))