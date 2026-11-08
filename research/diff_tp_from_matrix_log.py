"""
Diff TP baseline vs noisy по готовому логу матрицы.
е требует новых прогонов — читает output/ab_matrix_*.log.
"""
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TP_RE = re.compile(r"^\s+(\w+)\s+(buy|sell)\s+наш\s+int=([\d.]+)s\s+qty=\[([^\]]+)\].*?окно\s+(\S+)$")

def find_log():
    logs = sorted((BASE / "output").glob("ab_matrix_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        print("ог матрицы не найден"); sys.exit(1)
    return logs[0]

def parse_blocks(logfile, date):
    """озвращает (tp_base, tp_noisy) для даты, парсит блоки --- TP ---."""
    text = logfile.read_text(encoding="utf-8", errors="ignore")
    # азбиваем на блоки по разделителям ===== replay_alor ... =====
    parts = re.split(r"\n===== ", text)
    tp_base, tp_noisy = [], []
    for part in parts:
        if f"replay_alor.py {date}" not in part:
            continue
        is_noisy = "--noisy-qty" in part
        m = re.search(r"--- TP \(совпали.*?\n(.*?)(?=\n--- )", part, re.DOTALL)
        if not m:
            continue
        block = m.group(1)
        tp_list = []
        for line in block.split("\n"):
            match = TP_RE.match(line)
            if match:
                t, s, i, q, w = match.groups()
                tp_list.append((t, s, float(i), q, w))
        if is_noisy:
            tp_noisy = tp_list
        else:
            tp_base = tp_list
    return tp_base, tp_noisy

def main():
    date = sys.argv[1] if len(sys.argv) > 1 else "2026-09-07"
    log = find_log()
    print(f"ог: {log}")
    tp_base, tp_noisy = parse_blocks(log, date)
    print(f"baseline TP: {len(tp_base)}")
    print(f"noisy TP:    {len(tp_noisy)}")
    base_set = {(t, s, i) for t, s, i, q, w in tp_base}
    noisy_set = {(t, s, i) for t, s, i, q, w in tp_noisy}
    lost = sorted(base_set - noisy_set)
    base_dict = {(t, s, i): (q, w) for t, s, i, q, w in tp_base}
    print(f"\nотерянные TP (baseline - noisy): {len(lost)}")
    for t, s, i in lost:
        q, w = base_dict[(t, s, i)]
        print(f"  {t:6s} {s:4s} int={i:6.1f}s qty={q} {w}")

if __name__ == "__main__":
    main()
