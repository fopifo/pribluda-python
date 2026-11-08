"""
Сравнение TP baseline vs noisy за одну дату.
Прогоняет replay_alor дважды (baseline/noisy), сохраняет TP в jsonl,
находит потерянные TP, выводит детали.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
HISTORY = BASE / "data" / "replay_alor_history.jsonl"
TP_BASE = BASE / "data" / "tp_baseline.jsonl"
TP_NOISY = BASE / "data" / "tp_noisy.jsonl"
TMP_LOG = BASE / "output" / "compare_tp_tmp.log"

TP_RE = re.compile(r"^\s+(\w+)\s+(buy|sell)\s+наш\s+int=([\d.]+)s\s+qty=\[([^\]]+)\]")


def run_replay(date, noisy=False):
    """Прогоняет replay_alor + tw_compare, возвращает список TP."""
    cmd = [sys.executable, "research/replay_alor.py", date]
    if noisy:
        cmd.append("--noisy-qty")
    with open(TMP_LOG, "w", encoding="utf-8") as lf:
        subprocess.run(cmd, cwd=str(BASE), stdout=lf, stderr=lf,
                       text=True, encoding="utf-8", errors="replace")
    
    cmd = [sys.executable, "research/tw_compare.py", date,
           "--ours", "data/replay_alor_history.jsonl"]
    with open(TMP_LOG, "a", encoding="utf-8") as lf:
        subprocess.run(cmd, cwd=str(BASE), stdout=lf, stderr=lf,
                       text=True, encoding="utf-8", errors="replace")
    
    # Парсим TP из файла
    tp_list = []
    in_tp = False
    with open(TMP_LOG, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if "--- TP (совпали" in line:
                in_tp = True
                continue
            if in_tp and line.strip().startswith("---"):
                break
            if in_tp and line.strip():
                m = TP_RE.match(line)
                if m:
                    ticker, side, interval, qty = m.groups()
                    window_match = re.search(r"(\d{2}:\d{2}:\d{2}-\d{2}:\d{2}:\d{2})", line)
                    window = window_match.group(1) if window_match else ""
                    tp_list.append({
                        "ticker": ticker,
                        "side": side,
                        "interval": float(interval),
                        "qty": qty,
                        "window": window,
                    })
    return tp_list


def main():
    if len(sys.argv) < 2:
        print("Использование: python research/compare_tp_baseline_noisy.py 2026-09-07")
        sys.exit(1)
    
    date = sys.argv[1]
    print(f"[{date}] baseline...", flush=True)
    tp_base = run_replay(date, noisy=False)
    print(f"  baseline TP: {len(tp_base)}", flush=True)
    with open(TP_BASE, "w", encoding="utf-8") as f:
        for tp in tp_base:
            f.write(json.dumps(tp, ensure_ascii=False) + "\n")
    
    print(f"[{date}] noisy...", flush=True)
    tp_noisy = run_replay(date, noisy=True)
    print(f"  noisy TP: {len(tp_noisy)}", flush=True)
    with open(TP_NOISY, "w", encoding="utf-8") as f:
        for tp in tp_noisy:
            f.write(json.dumps(tp, ensure_ascii=False) + "\n")
    
    # Diff: baseline - noisy
    base_set = {(tp["ticker"], tp["side"], tp["interval"]) for tp in tp_base}
    noisy_set = {(tp["ticker"], tp["side"], tp["interval"]) for tp in tp_noisy}
    lost = base_set - noisy_set
    
    print(f"\nПотерянные TP: {len(lost)}")
    for ticker, side, interval in sorted(lost):
        tp = [t for t in tp_base if t["ticker"] == ticker and t["side"] == side and t["interval"] == interval][0]
        print(f"  {ticker:6s} {side:4s} int={interval:6.1f}s qty={tp['qty']} {tp['window']}")


if __name__ == "__main__":
    main()