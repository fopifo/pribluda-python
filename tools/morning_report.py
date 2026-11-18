"""
tools/morning_report.py — утренний ритуал: один запуск, один отчёт.

Не дублирует day_audit/replay_alor, а ОРКЕСТРИРУЕТ их и добавляет своё:
  A. Инвентарь входов (лента моста / Алор / Aniscan) + что докачать.
  B. ПОКРЫТИЕ МОСТОМ: по каждому активному тикеру — есть ли его сделки в
     ленте моста за день. Для дат до появления моста — честно пишет «н/д».
  C. replay_alor.py (истинные стороны) — с прогресс-баром.
  D. day_audit.py (матчинг v7 + классы FN + аудит FP) — с прогресс-баром.
  E. Сводка «что чинить» на основе цифр (парсит реальный формат day_audit).
  F. clean_tw_robots.py (дедуп эталона TW) — с прогресс-баром.
  G. tw_compare.py (сравнение с TW) — с прогресс-баром.
  H. tw_fn_summary.py (сводка по роботам TW) — с прогресс-баром.

Запуск: python tools/morning_report.py [YYYY-MM-DD]   # по умолчанию вчера
Отчёт:  output/morning_report_YYYY-MM-DD.txt

Прогресс-бары на всех фазах >5с (правило 2026-08-26).
"""
import sys
import json
import re
import time
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
OUT = BASE / "output"
MSK = ZoneInfo("Europe/Moscow")

# Первая дата, когда мост PbSdk реально писал ленту (см. заметки 2026-11-15).
# Раньше этой даты отсутствие pbsdk_trades — НОРМА, а не поломка.
BRIDGE_FIRST_DAY = "2026-11-15"


class Bar:
    """ASCII прогресс-бар: % | позиция | скорость | ETA (правило 2026-08-26)."""
    def __init__(self, total, label):
        self.total = max(int(total), 1)
        self.label = label
        self.n = 0
        self.t0 = time.time()
        self.last = 0.0
        self.tty = sys.stdout.isatty()
        self.lastpct = -1

    def add(self, k=1):
        self.n += k
        now = time.time()
        if now - self.last < 0.2 and self.n < self.total:
            return
        self.last = now
        self._render()

    def _render(self):
        pct = 100.0 * self.n / self.total
        filled = int(pct // 5)
        el = max(time.time() - self.t0, 1e-9)
        sp = self.n / el
        eta = (self.total - self.n) / sp if sp > 0 else 0
        line = (f"[{self.label}] [{'#' * filled}{'.' * (20 - filled)}] "
                f"{pct:3.0f}% | {self.n}/{self.total} | {sp:.0f}/с | ETA {eta:.0f}с")
        if self.tty:
            sys.stdout.write("\r" + line)
            sys.stdout.flush()
        else:
            if int(pct) // 10 != self.lastpct // 10:
                print(line, flush=True)
            self.lastpct = int(pct)

    def done(self):
        self.n = self.total
        self._render()
        if self.tty:
            sys.stdout.write("\n")
        else:
            print(flush=True)


class Spinner:
    """Для subprocess без известного объёма: крутится пока идёт процесс."""
    def __init__(self, label):
        self.label = label
        self.t0 = time.time()
        self.chars = "|/-\\"
        self.i = 0
        self.last = 0.0
        self.tty = sys.stdout.isatty()

    def tick(self):
        now = time.time()
        if now - self.last < 0.15:
            return
        self.last = now
        self.i = (self.i + 1) % 4
        el = now - self.t0
        line = f"[{self.label}] {self.chars[self.i]} {el:.0f}с"
        if self.tty:
            sys.stdout.write("\r" + line)
            sys.stdout.flush()

    def done(self):
        el = time.time() - self.t0
        if self.tty:
            sys.stdout.write(f"\r[{self.label}] готово за {el:.0f}с" + " " * 20 + "\n")
            sys.stdout.flush()
        else:
            print(f"[{self.label}] готово за {el:.0f}с", flush=True)


def load_active_tickers():
    p = BASE / "ticker_settings.json"
    out = set()
    try:
        doc = json.loads(p.read_text(encoding="utf-8-sig"))
        for sym, cfg in doc.items():
            if cfg.get("active", True):
                out.add(sym)
    except Exception as e:
        print(f"[!] ticker_settings.json: {e}")
    return out


def bridge_coverage(date_str):
    """B: покрытие мостом. Возвращает (seen_dict, status_text)."""
    tape = DATA / f"pbsdk_trades_{date_str}.jsonl"
    if not tape.exists():
        if date_str < BRIDGE_FIRST_DAY:
            return None, f"НЕТ (мост ещё не работал в {date_str} — это норма)"
        return None, "НЕТ (мост не писал сегодня — проверить окно PbSdkConsole)"
    seen = defaultdict(int)
    size = tape.stat().st_size
    bar = Bar(size, "мост")
    with open(tape, "rb") as fh:
        for raw in fh:
            bar.add(len(raw))
            try:
                d = json.loads(raw)
            except json.JSONDecodeError:
                continue
            sym = d.get("symbol")
            if sym:
                seen[sym] += 1
    bar.done()
    return seen, "есть"


def run_phase(label, cmd):
    """Запуск subprocess с прогресс-баром; возвращает (rc, stdout)."""
    sp = Spinner(label)
    proc = subprocess.Popen(cmd, cwd=str(BASE), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    buf = []
    while proc.poll() is None:
        line = proc.stdout.readline()
        if line:
            buf.append(line)
        sp.tick()
        time.sleep(0.05)
    rest = proc.stdout.read()
    if rest:
        buf.append(rest)
    sp.done()
    return proc.returncode, "".join(buf)


def parse_day_audit_summary(audit_out):
    """E: парсит реальный формат day_audit.py и вытаскивает метрики + классы FN."""
    summary = []
    if not audit_out.strip():
        summary.append("нет вывода day_audit — метрики недоступны (нет эталона).")
        return summary

    m_metrics = re.search(
        r'TP=(\d+)\s+\(overlap=(\d+),\s*param-only=(\d+)\)\s+FP=(\d+)\s+FN=(\d+)\s+re-detect=(\d+)',
        audit_out)
    if m_metrics:
        tp, overlap, param_only, fp, fn, redetect = m_metrics.groups()
        summary.append(f"TP={tp} (overlap={overlap}, param-only={param_only})  FP={fp}  FN={fn}  re-detect={redetect}")

    m_prec = re.search(r'Precision\s+([\d.]+)%\s+\((\d+)/(\d+)\)', audit_out)
    if m_prec:
        summary.append(f"Precision {m_prec.group(1)}% ({m_prec.group(2)}/{m_prec.group(3)})")

    m_rec = re.search(r'Recall\s+([\d.]+)%\s+\((\d+)/(\d+)\)', audit_out)
    if m_rec:
        summary.append(f"Recall {m_rec.group(1)}% ({m_rec.group(2)}/{m_rec.group(3)})")

    for cls in ("GRID-IN-TAPE", "BURST-AGG", "IRREGULAR", "THIN", "NO-TRADES"):
        m_cls = re.search(rf'{cls}\s*:\s*(\d+)', audit_out)
        if m_cls:
            summary.append(f"  {cls}: {m_cls.group(1)}")

    m_fp = re.search(
        r'АУДИТ FP: всего (\d+)\s+\(короткие <=120s:\s*(\d+),\s*длинные >120s:\s*(\d+)\)',
        audit_out)
    if m_fp:
        summary.append(f"FP всего={m_fp.group(1)} (короткие={m_fp.group(2)}, длинные={m_fp.group(3)})")

    if m_metrics and int(m_metrics.group(5)) > 0:
        grid_line = re.search(r'GRID-IN-TAPE\s*:\s*(\d+)', audit_out)
        if grid_line and int(grid_line.group(1)) > 0:
            summary.append(">>> ПРИОРИТЕТ: GRID-IN-TAPE > 0 = баг детектора (сетка в ленте есть, не поймали).")
        else:
            summary.append(">>> FN > 0, но GRID-IN-TAPE = 0 — роботы не видны в ленте (шум/агрегация).")

    return summary


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else (
        datetime.now(MSK) - timedelta(days=1)).strftime("%Y-%m-%d")

    print("=" * 78)
    print(f" УТРЕННИЙ РИТУАЛ — {date_str}")
    print("=" * 78)

    # ---------- A. инвентарь входов ----------
    tape_bridge = DATA / f"pbsdk_trades_{date_str}.jsonl"
    tape_alor = DATA / f"live_trades_{date_str}.jsonl"
    aniscan = DATA / f"aniscan_robots_{date_str}.jsonl"
    tw_file = DATA / f"tw_robots_{date_str}.jsonl"
    active = load_active_tickers()

    cov, bridge_status = bridge_coverage(date_str)

    print(f"\n[входы] активных тикеров в settings: {len(active)}")
    print(f"[входы] лента моста   : {bridge_status}")
    print(f"[входы] лента Алор    : {'есть' if tape_alor.exists() else 'НЕТ'}")
    print(f"[входы] эталон Aniscan: {'есть' if aniscan.exists() else 'НЕТ'}")
    print(f"[входы] эталон TW     : {'есть' if tw_file.exists() else 'НЕТ'}")

    missing = []
    if not aniscan.exists():
        missing.append(f"Aniscan за {date_str}:  python tools/aniscan_robot_history.py {date_str}")
    if not tape_alor.exists():
        missing.append(f"Алор за {date_str}:      python tools/alor_download_day.py {date_str}")
    if missing:
        print("\n[!] чего не хватает — выполни и повтори ритуал:")
        for m in missing:
            print("    " + m)

    # ---------- B. покрытие мостом ----------
    cov_lines = []
    if cov is not None:
        no_data = sorted([s for s in active if s not in cov])
        cov_lines.append(f"покрытие мостом: {len(cov)}/{len(active)} тикеров дали сделки")
        if no_data:
            cov_lines.append(f"НЕ видно мостом ({len(no_data)}): {', '.join(no_data[:40])}"
                             + (" ..." if len(no_data) > 40 else ""))
        print("\n[покрытие] " + cov_lines[0])
        if len(cov_lines) > 1:
            print("[покрытие] " + cov_lines[1])
    elif date_str < BRIDGE_FIRST_DAY:
        print("\n[покрытие] мост в эту дату не работал — покрытие не считаем (норма)")
    else:
        print("\n[покрытие] мост молчит сегодня — проверь окно PbSdkConsole")

    # ---------- C. replay_alor ----------
    replay_out = ""
    if tape_alor.exists():
        print("\n[1/5] replay_alor.py (истинные стороны)...")
        rc, replay_out = run_phase("replay",
                                   [sys.executable, "research/replay_alor.py", date_str])
        if rc != 0:
            print(f"[!] replay_alor rc={rc} — см. отчёт")
    else:
        print("\n[1/5] replay_alor ПРОПУЩЕН (нет ленты Алора)")

    # ---------- D. day_audit ----------
    audit_out = ""
    if aniscan.exists():
        print("\n[2/5] day_audit.py (матчинг v7 + классы FN + FP)...")
        rc, audit_out = run_phase("audit",
                                  [sys.executable, "research/day_audit.py", date_str])
        if rc != 0:
            print(f"[!] day_audit rc={rc} — см. отчёт")
    else:
        print("\n[2/5] day_audit ПРОПУЩЕН (нет эталона Aniscan)")

    # ---------- F. clean_tw_robots ----------
    tw_clean_out = ""
    if tw_file.exists():
        print("\n[3/5] clean_tw_robots.py (дедуп эталона TW)...")
        rc, tw_clean_out = run_phase("tw_clean",
                                     [sys.executable, "tools/clean_tw_robots.py", date_str])
    else:
        print("\n[3/5] clean_tw_robots ПРОПУЩЕН (нет эталона TW)")

    # ---------- G. tw_compare ----------
    tw_compare_out = ""
    if tw_file.exists():
        print("\n[4/5] tw_compare.py (сравнение с TW)...")
        rc, tw_compare_out = run_phase("tw_cmp",
                                       [sys.executable, "research/tw_compare.py", date_str])
    else:
        print("\n[4/5] tw_compare ПРОПУЩЕН (нет эталона TW)")

    # ---------- H. tw_fn_summary ----------
    tw_fn_out = ""
    if tw_file.exists():
        print("\n[5/5] tw_fn_summary.py (сводка по роботам TW)...")
        rc, tw_fn_out = run_phase("tw_fn",
                                  [sys.executable, "research/tw_fn_summary.py", date_str])
    else:
        print("\n[5/5] tw_fn_summary ПРОПУЩЕН (нет эталона TW)")

    # ---------- E. сводка «что чинить» ----------
    summary = parse_day_audit_summary(audit_out)

    # ---------- сборка отчёта ----------
    OUT.mkdir(exist_ok=True)
    rep = OUT / f"morning_report_{date_str}.txt"
    L = []
    L.append("=" * 78)
    L.append(f" УТРЕННИЙ РИТУАЛ — {date_str}  (создан {datetime.now(MSK).strftime('%H:%M:%S')})")
    L.append("=" * 78)
    L.append("\n--- ВХОДЫ ---")
    L.append(f"активных тикеров: {len(active)}")
    L.append(f"лента моста: {bridge_status} | "
             f"лента Алор: {'есть' if tape_alor.exists() else 'НЕТ'} | "
             f"Aniscan: {'есть' if aniscan.exists() else 'НЕТ'} | "
             f"TW: {'есть' if tw_file.exists() else 'НЕТ'}")
    if missing:
        L.append("не хватает:")
        for m in missing:
            L.append("  " + m)
    if cov_lines:
        L.append("\n--- ПОКРЫТИЕ МОСТОМ ---")
        L.extend(cov_lines)
    
    L.append("\n--- РЕЗЮМЕ ДЛЯ ДОРАБОТКИ (из day_audit) ---")
    L.extend(summary)
    
    L.append("\n--- ВЫВОД day_audit (полный) ---")
    L.append(audit_out if audit_out else "(не запускался)")
    
    L.append("\n--- ВЫВОД replay_alor (полный) ---")
    L.append(replay_out if replay_out else "(не запускался)")
    
    L.append("\n--- ВЫВОД clean_tw_robots ---")
    L.append(tw_clean_out if tw_clean_out else "(не запускался)")
    
    L.append("\n--- ВЫВОД tw_compare ---")
    L.append(tw_compare_out if tw_compare_out else "(не запускался)")
    
    L.append("\n--- ВЫВОД tw_fn_summary ---")
    L.append(tw_fn_out if tw_fn_out else "(не запускался)")
    
    L.append("\n" + "=" * 78)
    text = "\n".join(L)
    rep.write_text(text, encoding="utf-8")

    print("\n" + "=" * 78)
    print(f" РИТУАЛ ЗАВЕРШЁН. Отчёт сохранён: {rep}")
    print(" Можешь прочитать его и закрыть это окно.")
    print("=" * 78)


if __name__ == "__main__":
    main()