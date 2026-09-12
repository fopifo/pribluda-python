"""
Матрица A/B: baseline vs v12 noisy-qty по контрольным дням (эталон TW).
Прогресс: ОДНА живая строка — шаг N/12, секунды текущего шага (тикают
всегда), последний прогресс внутреннего скрипта (replay/tw_compare).
"""
import re
import subprocess
import sys
import time
import threading
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
LOGDIR = BASE / "output"
DATES = ["2026-09-03", "2026-09-04", "2026-09-07",
         "2026-09-08", "2026-09-09", "2026-09-10"]
TP_RE = re.compile(r"METRICS: TP=(\d+) FP=(\d+) FN=(\d+)")

_stop = threading.Event()


def tail_worker(logfile, state):
    """Одна живая строка прогресса поверх внутреннего лога."""
    while not _stop.is_set():
        frag = ""
        try:
            size = logfile.stat().st_size
            with open(logfile, "rb") as f:
                f.seek(max(0, size - 400))
                chunk = f.read().decode("utf-8", "ignore")
            parts = [p for p in (x.strip() for x in re.split(r"[\r\n]+", chunk)) if p]
            if parts:
                frag = parts[-1]
        except Exception:
            pass
        el = int(time.time() - state["t0"])
        line = f"[mat {state['step']}/{state['total']}] шаг {el:>4}с | {frag[:78]}"
        print("\r" + line.ljust(110), end="", flush=True)
        _stop.wait(2.0)


def run(cmd, logfile):
    with open(logfile, "a", encoding="utf-8") as lf:
        lf.write("\n===== " + " ".join(cmd) + " =====\n")
        lf.flush()
        return subprocess.run(cmd, cwd=str(BASE), stdout=lf, stderr=lf,
                              text=True, encoding="utf-8", errors="replace").returncode


def parse(logfile, start_pos):
    text = Path(logfile).read_text(encoding="utf-8", errors="ignore")[start_pos:]
    m = TP_RE.findall(text)
    if not m:
        lines = text.strip().split("\n")
        print("\n  [parse-fail] хвост лога:")
        for ln in lines[-8:]:
            print("    " + ln[:120])
        return None
    return tuple(int(x) for x in m[-1])


def main():
    dates = sys.argv[1:] or DATES
    LOGDIR.mkdir(exist_ok=True)
    logfile = LOGDIR / ("ab_matrix_%s.log" % time.strftime("%Y%m%d_%H%M%S"))
    logfile.touch()
    rows = []
    total = len(dates) * 2
    state = {"step": 0, "total": total, "t0": time.time()}

    thr = threading.Thread(target=tail_worker, args=(logfile, state), daemon=True)
    thr.start()

    done = 0
    try:
        for d in dates:
            for mode, extra in (("base", []), ("noisy", ["--noisy-qty"])):
                done += 1
                state["step"] = done
                state["t0"] = time.time()
                pos = logfile.stat().st_size
                rc1 = run([sys.executable, "research/replay_alor.py", d] + extra, logfile)
                rc2 = run([sys.executable, "research/tw_compare.py", d,
                           "--ours", "data/replay_alor_history.jsonl"], logfile)
                res = parse(logfile, pos)
                dt = time.time() - state["t0"]
                print()  # newline после живой строки
                if res is None or rc1 or rc2:
                    rows.append((d, mode, None))
                    print(f"[{done}/{total}] {d} {mode} ОШИБКА rc={rc1}/{rc2} ({dt:.0f}s)")
                else:
                    tp, fp, fn = res
                    prec = 100.0 * tp / (tp + fp) if (tp + fp) else 0.0
                    rec = 100.0 * tp / (tp + fn) if (tp + fn) else 0.0
                    rows.append((d, mode, (tp, fp, fn, prec, rec)))
                    print(f"[{done}/{total}] {d} {mode} TP={tp} FP={fp} FN={fn} "
                          f"Prec={prec:.2f}% Rec={rec:.2f}% ({dt:.0f}s)")
    finally:
        _stop.set()
        thr.join(timeout=1)
        print()

    print("=" * 78)
    print("СВОДНАЯ ТАБЛИЦА A/B: v12 noisy-qty vs baseline (эталон T-Widgets)")
    print("=" * 78)
    print("%-12s %-6s %6s %7s %7s %7s %7s" % ("дата", "режим", "TP", "FP", "FN", "Prec%", "Rec%"))
    print("-" * 78)
    for d, mode, res in rows:
        if res is None:
            print("%-12s %-6s %6s %7s %7s %7s %7s" % (d, mode, "-", "-", "-", "-", "-"))
        else:
            tp, fp, fn, prec, rec = res
            print("%-12s %-6s %6d %7d %7d %7.2f %7.2f" % (d, mode, tp, fp, fn, prec, rec))
    print("-" * 78)
    for label, m in (("ИТОГО base", "base"), ("ИТОГО noisy", "noisy")):
        vals = [r[2] for r in rows if r[1] == m and r[2] is not None]
        if vals:
            s_tp = sum(v[0] for v in vals)
            s_fp = sum(v[1] for v in vals)
            s_fn = sum(v[2] for v in vals)
            s_prec = 100.0 * s_tp / (s_tp + s_fp) if (s_tp + s_fp) else 0.0
            s_rec = 100.0 * s_tp / (s_tp + s_fn) if (s_tp + s_fn) else 0.0
            print("%-12s %-6s %6d %7d %7d %7.2f %7.2f" % (label, "", s_tp, s_fp, s_fn, s_prec, s_rec))
    print()
    print("Полный лог: %s" % logfile)


if __name__ == "__main__":
    main()