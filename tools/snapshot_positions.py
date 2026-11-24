"""
tools/snapshot_positions.py — снапшоты позиций моста для точного расчёта фандинга.

Стратегия:
- Сохраняет ТОЛЬКО символы с ненулевой позицией (экономия места).
- Запускается по таймеру каждые 5 мин во время сессии (10:00–18:50 МСК).
- Финальный снапшот в 18:50 МСК (сразу после вечернего клиринга MOEX).
- Если позиция не изменилась с прошлого снапшота — не дублирует файл.
- Файлы: data/positions_YYYY-MM-DD_HHMM.json

Использование:
    python tools/snapshot_positions.py          # один снапшот сейчас
    python tools/snapshot_positions.py --loop   # цикл каждые 5 мин (для фонового запуска)
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

BRIDGE_STATE = DATA / "bridge_state.json"
SESSION_START = 10 * 60      # 10:00 в минутах
SESSION_END = 18 * 60 + 50   # 18:50 в минутах
POLL_INTERVAL = 300          # 5 минут


def load_positions():
    """Читает bridge_state.json, возвращает только ненулевые позиции."""
    if not BRIDGE_STATE.exists():
        return {}
    try:
        with open(BRIDGE_STATE, encoding="utf-8") as f:
            state = json.load(f)
        syms = state.get("symbols", {})
        return {
            s: {
                "position": i.get("position", 0),
                "avg_entry": i.get("avg_entry", 0),
                "realized": i.get("realized", 0),
            }
            for s, i in syms.items()
            if i.get("position", 0) != 0
        }
    except Exception:
        return {}


def save_snapshot(positions, label=""):
    """Сохраняет снапшот, если позиции изменились с прошлого."""
    now = datetime.now(MSK)
    fname = f"positions_{now.strftime('%Y-%m-%d_%H%M')}.json"
    fpath = DATA / fname

    # Проверяем, не дублируем ли мы предыдущий снапшот
    existing = sorted(DATA.glob(f"positions_{now.strftime('%Y-%m-%d')}_*.json"))
    if existing:
        try:
            with open(existing[-1], encoding="utf-8") as f:
                prev = json.load(f)
            if prev.get("positions") == positions:
                return None  # позиции не изменились — не дублируем
        except Exception:
            pass

    snapshot = {
        "timestamp": now.isoformat(),
        "label": label,
        "positions": positions,
    }
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    size_kb = fpath.stat().st_size / 1024
    print(f"[snapshot] {fname} ({size_kb:.1f} КБ, {len(positions)} позиций) {label}")
    return fpath


def in_session():
    """Проверяет, находимся ли мы в торговой сессии."""
    now = datetime.now(MSK)
    minutes = now.hour * 60 + now.minute
    return SESSION_START <= minutes <= SESSION_END


def main():
    loop_mode = "--loop" in sys.argv
    DATA.mkdir(exist_ok=True)

    if loop_mode:
        print("[snapshot] Режим цикла: каждые 5 мин во время сессии (10:00–18:50 МСК)")
        print("[snapshot] Ctrl+C для остановки")
        last_positions = None
        while True:
            try:
                if in_session():
                    positions = load_positions()
                    if positions != last_positions:
                        save_snapshot(positions, label="auto")
                        last_positions = positions
                    # Финальный снапшот в 18:50
                    now = datetime.now(MSK)
                    if now.hour == 18 and now.minute >= 50:
                        save_snapshot(positions, label="clearing")
                time.sleep(POLL_INTERVAL)
            except KeyboardInterrupt:
                print("\n[snapshot] Остановлено.")
                break
            except Exception as e:
                print(f"[snapshot] Ошибка: {e}")
                time.sleep(POLL_INTERVAL)
    else:
        positions = load_positions()
        if not positions:
            print("[snapshot] Нет открытых позиций (мост не отдаёт или все нули).")
        else:
            save_snapshot(positions, label="manual")


if __name__ == "__main__":
    main()