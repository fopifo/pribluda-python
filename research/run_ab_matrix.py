"""
Приблуда на python — матрица A/B: baseline vs v12 noisy-qty по контрольным
дням, эталон T-Widgets. Гоняет replay_alor.py + tw_compare.py парами
(реплей затем компаратор по свежей истории), парсит TP/FP/FN, считает
Precision/Recall сам с точностью 2 знака (правило точности отчётов).

Полный лог всех прогонов: output/ab_matrix_<метка>.log
На консоль — строка по каждому прогону и сводная таблица в конце.

Использование:
    python research/run_ab_matrix.py               # все 6 контрольных дней
    python research/run_ab_matrix.py 2026-09-07    # только указанные даты
"""
import re
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
LOGDIR = BASE / "output"
DATES = ["2026-09-03", "2026-09-04", "2026-09-07",
         "2026-09-08", "2026-09-09", "2026-09-10"]

TP_RE = re.compile(r"TP:\s*(\d+)\s+FP:\s*(\d+)\s+FN\(реальные\):\s*(\d+)")


def run(cmd, logfile):
    with open(logfile, "a", encoding="utf-8") as lf:
        lf.write(f"\n===== {' '.join(cmd)} =====\n")
        lf.flush()
        p = subprocess.run(cmd, cwd=str(BASE), stdout=lf, stderr=lf, text=True)
    return p.returncode


def parse(logfile, start_pos):
    text = Path(logfile).read_text(encoding="utf-8", errors="ignore")[start_pos:]
    m = TP_RE.search(text)
    if not m:
        return None
    return tuple(int(x) for x in m.groups())


def main():
    dates = sys.argv[1:] or DATES
    LOGDIR.mkdir(exist_ok=True)
    logfile = LOGDIR / f"ab_matrix_{time.strftime('%Y%m%d_%H%M%S')}.log"
    rows = []
    for d in dates:
        for mode, extra in (("base", []), ("noisy", ["--noisy-qty"])):
            t0 = time.time()
            pos = logfile.stat().st_size if logfile.exists() else 0
            rc1 = run([sys.executable, "research/replay_alor.py", d] + extra, logfile)
            rc2 = run([sys.executable, "research/tw_compare.py", d,
                       "--ours", "data/replay_alor_history.jsonl"], logfile)
            res = parse(logfile, pos)
            dt = time.time() - t0
            if res is None or rc1 or rc2:
                rows.append((d, mode, None))
                print(f"{d} {mode:5s} ОШИБКА rc={rc1}/{rc2} ({dt:.0f}s)", flush=True)
            else:
                tp, fp, fn = res
                prec = 100.0 * tp / (tp + fp) if (tp + fp) else 0.0
                rec = 100.0 * tp / (tp + fn) if (tp + fn) else 0.0
                rows.append((d, mode, (tp, fp, fn, prec, rec)))
                print(f"{d} {mode:5s} TP={tp:5d} FP={fp:6d} FN={fn:6d} "
                      f"Prec={prec:.2f}% Rec={rec:.2f}% ({dt:.0f}s)", flush=True)
    print()
    print("=" * 74)
    print("СВОДНАЯ ТАБЛИЦА A/B: v12 noisy-qty vs baseline (эталон T-Widgets)")
    print("=" * 74)
    print(f"{'дата':12s} {'режим':6s} {'TP':>6s} {'FP':>7s} {'FN':>7s} "
          f"{'Prec%':>7s} {'Rec%':>7s}")
    for d, mode, res in rows:
        if res is None:
            print(f"{d:12s} {mode:6s}      —       —       —       —       —")
        else:
            tp, fp, fn, prec, rec = res
            print(f"{d:12s} {mode:6s} {tp:6d} {fp:7d} {fn:7d} "
                  f"{prec:7.2f} {rec:7.2f}")
    print()
    print(f"Полный лог: {logfile}")


if __name__ == "__main__":
    main()