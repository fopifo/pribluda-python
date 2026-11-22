"""
ai_solution_v1/combined.py
==========================
Связыватель ленты и стакана. Ищет Мега-Сигналы и пишет в result.txt.
"""
import json
import time
import sys
from pathlib import Path

# Импорты из твоего проекта
from ai_solution_v1.detect.interval import IntervalDetector, parse_trade
from ai_solution_v1.orderbook import OrderbookDetector, SpoofEvent, parse_snapshot, OrderbookConfig


def progress_bar(label: str, current_bytes: int, total_bytes: int, start_time: float, extra: str = ""):
    if total_bytes <= 0:
        return
    now = time.perf_counter()
    pct = (current_bytes / total_bytes) * 100
    rate = current_bytes / max(now - start_time, 1e-9)
    eta = (total_bytes - current_bytes) / max(rate, 1)
    
    bar_len = 20
    filled = int(bar_len * current_bytes // total_bytes)
    bar = "#" * filled + "." * (bar_len - filled)
    
    sys.stdout.write(f"\r[{label}] [{bar}] {pct:5.1f}% | {rate/1024/1024:.1f} MB/s | ETA {eta:.0f}с | {extra}")
    sys.stdout.flush()


def run_combined(tape_path: Path, ob_path: Path, out_path: Path, window_ms: int = 60000):
    # 1. Прогон ленты
    print("[combined] Запуск ленточного детектора...")
    det_tape = IntervalDetector()
    tape_size = tape_path.stat().st_size
    tape_read = 0
    start_tape = time.perf_counter()
    
    with open(tape_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            tape_read += len(line.encode('utf-8'))
            try:
                raw = json.loads(line)
                trade = parse_trade(raw)
                if trade:
                    det_tape.on_trade(trade)
            except Exception:
                continue
            
            if i % 10000 == 0:
                progress_bar("tape", tape_read, tape_size, start_tape, f"сигналов: {len(det_tape.reported)}")
    
    progress_bar("tape", tape_size, tape_size, start_tape)
    print()
    
    signals = det_tape.reported
    print(f"[combined] Найдено {len(signals)} интервальных роботов в ленте.")
    
    # 2. Прогон стакана
    print("[combined] Запуск стаканного детектора...")
    det_ob = OrderbookDetector(OrderbookConfig())
    ob_events = []
    ob_size = ob_path.stat().st_size
    ob_read = 0
    start_ob = time.perf_counter()
    
    with open(ob_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            ob_read += len(line.encode('utf-8'))
            try:
                raw = json.loads(line)
                snap = parse_snapshot(raw)
                if snap:
                    evs = det_ob.on_snapshot(snap)
                    ob_events.extend(evs)
            except Exception:
                continue
                
            if i % 10000 == 0:
                progress_bar("ob  ", ob_read, ob_size, start_ob, f"событий: {len(ob_events)}")
                
    progress_bar("ob  ", ob_size, ob_size, start_ob)
    print()
    print(f"[combined] Найдено {len(ob_events)} событий в стакане.")
    
    # 3. Связывание
    print("[combined] Поиск совпадений...")
    mega_signals = []
    
    for sig in signals:
        for ev in ob_events:
            if ev.symbol != sig.symbol:
                continue
            
            sig_start = sig.start_ms
            sig_end = sig.end_ms
            ev_time = ev.ts_disappeared_ms if isinstance(ev, SpoofEvent) else ev.ts_ms
            
            # Если событие стакана произошло в течение жизни серии (плюс-минус окно)
            if (sig_start - window_ms <= ev_time <= sig_end + window_ms):
                mega_signals.append({
                    "symbol": sig.symbol,
                    "side": "buy" if sig.side == 1 else "sell",
                    "tape_interval_sec": sig.base_interval_ms / 1000,
                    "tape_hits": sig.hits,
                    "ob_type": ev.kind,
                    "ob_price": ev.price,
                    "ob_qty": ev.qty,
                    "delta_ms": min(abs(ev_time - sig_start), abs(ev_time - sig_end))
                })
    
    # 4. Формирование отчета
    report_lines = []
    report_lines.append("=" * 60)
    report_lines.append(f" МЕГА-СИГНАЛЫ (Лента + Стакан)")
    report_lines.append("=" * 60)
    
    if not mega_signals:
        report_lines.append("Совпадений не найдено.")
    else:
        mega_signals.sort(key=lambda x: x["delta_ms"])
        for ms in mega_signals[:30]: # Берем первые 30, чтобы не спамить
            report_lines.append(
                f"{ms['symbol']:<10} {ms['side']:<4} | Лента: {ms['tape_interval_sec']:.1f}с ({ms['tape_hits']} уд.) | "
                f"Стакан: {ms['ob_type']:<5} {ms['ob_price']} (qty={ms['ob_qty']}) | Δ={ms['delta_ms']}мс"
            )
        report_lines.append(f"\n... всего найдено {len(mega_signals)} мега-сигналов.")
        
    report_text = "\n".join(report_lines)
    print(report_text)
    
    # 5. Запись в файл для Раннера
    out_path.write_text(report_text, encoding='utf-8')
    print(f"\n[combined] Результат записан в {out_path}")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Использование: python -m ai_solution_v1.combined <лента.jsonl> <стакан.jsonl> <result.txt>")
        sys.exit(1)
        
    run_combined(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))