"""
combined.py
===========
Архитектурный связыватель: анализирует ленту сделок и стакан, 
находит совпадения (Мега-Сигналы) и сохраняет отчет в result.txt для runner.py.
НЕ содержит логики торговли. Только анализ и запись.
"""
import json
import time
import sys
from pathlib import Path
from typing import List, Dict, Any

# Импорты из ядра проекта
from ai_solution_v1.detect.interval import IntervalDetector, parse_trade
from ai_solution_v1.orderbook import OrderbookDetector, SpoofEvent, parse_snapshot, OrderbookConfig

def progress_bar(label: str, current_bytes: int, total_bytes: int, start_time: float, extra: str = "") -> None:
    """
    Обязательный ASCII прогресс-бар для операций > 5 секунд.
    Показывает: проценты, скорость, ETA и доп. информацию.
    """
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

def run_combined(tape_path: Path, ob_path: Path, out_path: Path, window_ms: int = 60000) -> List[Dict[str, Any]]:
    if not tape_path.exists():
        print(f"[ОШИБКА] Файл ленты не найден: {tape_path}")
        return []
    if not ob_path.exists():
        print(f"[ОШИБКА] Файл стакана не найден: {ob_path}")
        return []

    # 1. Прогон ленты сделок
    print(f"[combined] Запуск ленточного детектора: {tape_path.name}")
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
            except json.JSONDecodeError:
                continue
            except Exception:
                pass
            
            if i % 5000 == 0:
                progress_bar("tape", tape_read, tape_size, start_tape, f"сигналов: {len(det_tape.reported)}")
    
    progress_bar("tape", tape_size, tape_size, start_tape)
    print()
    
    signals = det_tape.reported
    print(f"[combined] Найдено {len(signals)} интервальных роботов в ленте.")
    
    # 2. Прогон стакана
    print(f"[combined] Запуск стаканного детектора: {ob_path.name}")
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
            except json.JSONDecodeError:
                continue
            except Exception:
                pass
                
            if i % 5000 == 0:
                progress_bar("ob  ", ob_read, ob_size, start_ob, f"событий: {len(ob_events)}")
                
    progress_bar("ob  ", ob_size, ob_size, start_ob)
    print()
    print(f"[combined] Найдено {len(ob_events)} событий в стакане.")
    
    # 3. Связывание (Кросс-анализ)
    print("[combined] Поиск совпадений (Мега-Сигналы)...")
    mega_signals = []
    
    # Оптимизация: фильтруем события стакана только по тикерам из сигналов ленты
    tape_tickers = {sig.symbol for sig in signals}
    filtered_ob_events = [ev for ev in ob_events if ev.symbol in tape_tickers]
    
    print(f"[combined] Фильтрация: {len(filtered_ob_events)} событий стакана из {len(ob_events)} совпадают по тикерам.")
    
    for sig in signals:
        for ev in filtered_ob_events:
            if ev.symbol != sig.symbol:
                continue
            
            sig_start = sig.start_ms
            sig_end = sig.end_ms
            ev_time = ev.ts_disappeared_ms if isinstance(ev, SpoofEvent) else ev.ts_ms
            
            # Проверка попадания в временное окно
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
    report_lines.append("=" * 80)
    report_lines.append(f" МЕГА-СИГНАЛЫ (Лента + Стакан) | Окно: {window_ms}мс")
    report_lines.append("=" * 80)
    report_lines.append(f"Обработано сигналов ленты: {len(signals)}")
    report_lines.append(f"Обработано событий стакана: {len(ob_events)}")
    report_lines.append("-" * 80)
    
    if not mega_signals:
        report_lines.append("Совпадений не найдено.")
    else:
        # Сортируем по минимальной задержке (delta_ms)
        mega_signals.sort(key=lambda x: x["delta_ms"])
        report_lines.append(f"Найдено совпадений: {len(mega_signals)} (показаны топ-30)")
        report_lines.append("-" * 80)
        for ms in mega_signals[:30]:
            report_lines.append(
                f"{ms['symbol']:<10} {ms['side']:<4} | Лента: {ms['tape_interval_sec']:.1f}с ({ms['tape_hits']} уд.) | "
                f"Стакан: {ms['ob_type']:<5} {ms['ob_price']} (qty={ms['ob_qty']}) | Δ={ms['delta_ms']}мс"
            )
        if len(mega_signals) > 30:
            report_lines.append(f"\n... и ещё {len(mega_signals) - 30} совпадений.")
        
    report_text = "\n".join(report_lines)
    print("\n" + report_text)
    
    # 5. Запись в файл для Раннера
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report_text, encoding='utf-8')
    print(f"\n[combined] Результат успешно записан в: {out_path.absolute()}")
    
    return mega_signals

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Использование: python combined.py <путь_к_ленте.jsonl> <путь_к_стакану.jsonl> <путь_к_результату.txt>")
        print("Пример: python combined.py data/tape.jsonl data/ob.jsonl result.txt")
        sys.exit(1)
        
    tape_file = Path(sys.argv[1])
    ob_file = Path(sys.argv[2])
    out_file = Path(sys.argv[3])
    
    run_combined(tape_file, ob_file, out_file)