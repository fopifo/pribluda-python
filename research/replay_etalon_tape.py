"""
Приблуда на python — реплей ленты ЭТАЛОНА (research/etalon_sngsp_2026-08-21.csv).
Стороны в файле НАСТОЯЩИЕ (не tick-rule) — это "земля" для проверки детектора.
Прогоняем боевой детектор SNGSP в двух вариантах:
  A) текущие настройки из ticker_settings.json (min_qty=50);
  B) min_qty=10 (сняли порог) — видим, теряем ли робота из-за порога.
Печатаем закрытые серии (LEN>=4) и активные на конец ленты (LEN>=3).
Эталонный робот: SNGSP buy 64-65 @~15s.
Запуск: python research/replay_etalon_tape.py
"""
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
TAPE = BASE / "research" / "etalon_sngsp_2026-08-21.csv"
SYMBOL = "SNGSP"
DATE = "2026-08-21"


def load_trades():
    trades = []
    with open(TAPE, encoding="utf-8") as f:
        f.readline()  # шапка price;qty;side;trade_time
        for line in f:
            line = line.strip()
            if not line:
                continue
            p = line.split(";")
            if len(p) < 4:
                continue
            try:
                price = float(p[0])
                qty = int(float(p[1]))
                side = p[2].strip().lower()
                ts = datetime.fromisoformat(f"{DATE}T{p[3]}").replace(tzinfo=MSK).timestamp()
            except ValueError:
                continue
            if side not in ("buy", "sell"):
                continue
            trades.append({"symbol": SYMBOL, "qty": qty, "price": price,
                           "side": side, "timestamp": int(ts * 1000)})
    trades.sort(key=lambda t: t["timestamp"])
    return trades


def run(trades, min_qty_override=None, label=""):
    ov = dict(load_settings().get(SYMBOL, {}))
    if min_qty_override is not None:
        ov["min_qty"] = min_qty_override
    mq = ov.get("min_qty", 10)
    dets = [IntervalRobotDetector(SYMBOL, c) for c in get_detector_configs(SYMBOL, mq, ov)]
    signals = []
    for t in trades:
        for d in dets:
            signals.extend(d.on_trade(t))
    for d in dets:
        signals.extend(d.flush())
    now_ts = trades[-1]["timestamp"] / 1000.0 if trades else 0.0
    active = []
    for d in dets:
        active.extend(r for r in d.get_active_snapshot(now_ts) if r["repeats"] >= 3)
    print(f"--- {label}: сделок={len(trades)}, закрытых серий={len(signals)}, активных(LEN>=3)={len(active)}")
    for s in sorted(signals, key=lambda s: s.start_ts):
        if s.repeats < 4:
            continue
        st = datetime.fromtimestamp(s.start_ts, tz=MSK).strftime("%H:%M")
        en = datetime.fromtimestamp(s.end_ts, tz=MSK).strftime("%H:%M")
        qty = "-".join(str(q) for q in s.qty_variants)
        print(f"  {s.side:4} qty={qty:<8} int={s.interval_avg:5.1f}s LEN={s.repeats:3}  {st}-{en}")
    for r in sorted(active, key=lambda r: r["start_ts"]):
        st = datetime.fromtimestamp(r["start_ts"], tz=MSK).strftime("%H:%M")
        qty = "-".join(str(q) for q in r["qty_variants"])
        iv = f"{r['interval']:.1f}" if r["interval"] else "-"
        print(f"  АКТИВНА {r['side']:4} qty={qty:<8} int={iv:>5}s LEN={r['repeats']:3}  c {st}")


def main():
    if not TAPE.exists():
        print(f"Лента не найдена: {TAPE}")
        print("Скопируй файл эталона в research/etalon_sngsp_2026-08-21.csv")
        return
    trades = load_trades()
    if not trades:
        print("Лента пустая или не распознана.")
        return
    run(trades, None, "A) ТЕКУЩИЕ настройки (min_qty из настроек)")
    print()
    run(trades, 10, "B) min_qty=10 (порог снят)")


if __name__ == "__main__":
    main()