"""
tools/morning_report.py — утренний ритуал: один запуск, один отчёт.

Фазы:
  0.  Авто-докачка Алора за дату (неинтерактивный).
  0b. Ожидание Aniscan: расширение Chrome (живёт в браузере, открыто батником
      start_all.bat шаг 2/7) само тянет историю за today+yesterday. Ждём до
      180 сек (запас для слабого ноута), опрашивая файл каждые 5 сек.
  A.  Инвентарь входов (лента моста / Алор / Aniscan / TW) + что докачать вручную.
  B.  Покрытие мостом по активным тикерам.
  1.  replay_alor.py (истинные стороны).
  2.  day_audit.py (матчинг v7 + классы FN + аудит FP).
  3.  clean_tw_robots.py (дедуп эталона T-Widgets).
  4.  tw_compare.py (сравнение с TW: TP/FP/FN).
  5.  tw_fn_summary.py (кого мы режем на входе по min_qty).
  E.  Сводка «что чинить» из цифр day_audit.

Запуск: python tools/morning_report.py [YYYY-MM-DD]   # по умолчанию вчера
"""
import sys
import os
import json
import re
import time
import subprocess
import gc
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Принудительный UTF-8 для консоли Windows
if hasattr(sys.stdout, "reconfigure"):
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
if hasattr(sys.stderr, "reconfigure"):
    try: sys.stderr.reconfigure(encoding="utf-8")
    except Exception: pass

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
OUT = BASE / "output"
MSK = ZoneInfo("Europe/Moscow")

BRIDGE_FIRST_DAY = "2026-11-15"

# Запас ожидания Aniscan (сек). Расширение в браузере должно успеть выкачать
# историю за время от старта Chrome (батник start_all.bat) до запуска отчёта.
# На слабом ноуте: загрузка Chrome ~15с + вкладки ~10с + aniscan 40 страниц ~15с
# + буфер на гонку/перезапуск моста ~30с = итого ~70с. Закладываем 180с (x2.5).
ANISCAN_WAIT_MAX_SEC = 180
ANISCAN_POLL_INTERVAL_SEC = 5

CHILD_ENV = dict(os.environ)
CHILD_ENV["PYTHONUTF8"] = "1"
CHILD_ENV["PYTHONIOENCODING"] = "utf-8"


class Bar:
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
    proc = subprocess.Popen(cmd, cwd=str(BASE), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", env=CHILD_ENV)
    buf = []
    while proc.poll() is None:
        line = proc.stdout.readline()
        if line:
            sys.stdout.write(line)
            sys.stdout.flush()
            buf.append(line)
        else:
            time.sleep(0.03)
    rest = proc.stdout.read()
    if rest:
        sys.stdout.write(rest)
        sys.stdout.flush()
        buf.append(rest)
    return proc.returncode, "".join(buf)


def _clean_for_report(text):
    keep = []
    for ln in text.splitlines():
        if "ETA" in ln and ("[#" in ln or "[." in ln or "] " in ln):
            continue
        keep.append(ln)
    return "\n".join(keep)


def parse_day_audit_summary(audit_out):
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
            summary.append(">>> ПРОРИТЕТ: GRID-IN-TAPE > 0 = баг детектора (сетка в ленте есть, не поймали).")
        else:
            summary.append(">>> FN > 0, но GRID-IN-TAPE = 0 — роботы не видны в ленте (шум/агрегация).")

    return summary


def wait_for_aniscan(date_str):
    """0b: ждём появления файла aniscan_robots_<дата>.jsonl. Расширение Chrome
    само его тянет (живёт в браузере, открыто start_all.bat). Возвращает
    (exists: bool, waited_sec: int)."""
    an_path = DATA / f"aniscan_robots_{date_str}.jsonl"
    if an_path.exists() and an_path.stat().st_size > 0:
        return True, 0

    waited = 0
    chars = "|/-\\"
    ci = 0
    print(f"[0b] Ожидание Aniscan (расширение в браузере): макс {ANISCAN_WAIT_MAX_SEC}с, опрос каждые {ANISCAN_POLL_INTERVAL_SEC}с...")
    while waited < ANISCAN_WAIT_MAX_SEC:
        sys.stdout.write(f"\r[0b] {chars[ci % 4]} жду {waited:>3}/{ANISCAN_WAIT_MAX_SEC}с  (файл: {an_path.name})")
        sys.stdout.flush()
        time.sleep(ANISCAN_POLL_INTERVAL_SEC)
        waited += ANISCAN_POLL_INTERVAL_SEC
        ci += 1
        if an_path.exists() and an_path.stat().st_size > 0:
            sys.stdout.write(f"\r[0b] ✅ Aniscan появился через {waited}с ({an_path.name}, {an_path.stat().st_size} байт)\n")
            sys.stdout.flush()
            return True, waited
    sys.stdout.write(f"\r[0b] ❌ Aniscan не появился за {ANISCAN_WAIT_MAX_SEC}с. Проверь: Chrome запущен? вкладка aniscan.ru открыта? расширение Pribluda Aniscan Intercept активно?\n")
    sys.stdout.flush()
    return False, waited


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else (
        datetime.now(MSK) - timedelta(days=1)).strftime("%Y-%m-%d")

    print("=" * 78)
    print(f" УТРЕННИЙ РИТУАЛ — {date_str}")
    print("=" * 78)

    # ---------- 0. авто-докачка Алора ----------
    tape_alor = DATA / f"live_trades_{date_str}.jsonl"
    if not tape_alor.exists():
        print(f"\n[0/6] Авто-докачка Алора за {date_str} (неинтерактивный)...")
        rc, _ = run_phase("alor_dl", [sys.executable, "tools/alor_download_day.py", date_str])
        if rc != 0:
            print(f"[!] alor_download_day rc={rc}")
        tape_alor = DATA / f"live_trades_{date_str}.jsonl"
    else:
        print(f"\n[0/6] Алор за {date_str} уже есть — докачка не нужна")

    # ---------- 0b. ожидание Aniscan ----------
    print()
    aniscan_ok, waited = wait_for_aniscan(date_str)
    aniscan = DATA / f"aniscan_robots_{date_str}.jsonl"

    # ---------- A. инвентарь входов ----------
    tw_file = DATA / f"tw_robots_{date_str}.jsonl"
    active = load_active_tickers()

    cov, bridge_status = bridge_coverage(date_str)

    print(f"\n[входы] активных тикеров в settings: {len(active)}")
    print(f"[входы] лента моста   : {bridge_status}")
    print(f"[входы] лента Алор    : {'есть' if tape_alor.exists() else 'НЕТ'}")
    print(f"[входы] эталон Aniscan: {'есть ('+str(aniscan.stat().st_size)+' байт)' if aniscan.exists() else 'НЕТ (ждали '+str(waited)+'с)'}")
    print(f"[входы] эталон TW     : {'есть' if tw_file.exists() else 'НЕТ'}")

    missing = []
    if not aniscan.exists() or not aniscan_ok:
        missing.append(f"Aniscan за {date_str}: расширение не выкачало за {ANISCAN_WAIT_MAX_SEC}с. "
                       f"Открой https://aniscan.ru/robot-history/table в Chrome, затем запусти ритуал ещё раз.")
    if missing:
        print("\n[!] чего не хватает:")
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

    # ---------- 1. replay_alor ----------
    replay_out = ""
    if tape_alor.exists():
        print("\n[1/6] replay_alor.py (истинные стороны)...")
        rc, replay_out = run_phase("replay", [sys.executable, "research/replay_alor.py", date_str])
        print("\n[Очистка памяти после replay]...", file=sys.stderr)
        gc.collect()
        if rc != 0:
            print(f"[!] replay_alor rc={rc}")
    else:
        print("\n[1/6] replay_alor ПРОПУЩЕН (нет ленты Алора)")

    # ---------- 2. day_audit ----------
    audit_out = ""
    if aniscan.exists():
        print("\n[2/6] day_audit.py (матчинг v7 + классы FN + FP)...")
        rc, audit_out = run_phase("audit", [sys.executable, "research/day_audit.py", date_str])
        print("\n[Очистка памяти после day_audit]...", file=sys.stderr)
        gc.collect()
        if rc != 0:
            print(f"[!] day_audit rc={rc}")
    else:
        print("\n[2/6] day_audit ПРОПУЩЕН (нет эталона Aniscan)")

    # ---------- 3. clean_tw_robots ----------
    tw_clean_out = ""
    if tw_file.exists():
        print("\n[3/6] clean_tw_robots.py (дедуп эталона TW)...")
        rc, tw_clean_out = run_phase("tw_clean", [sys.executable, "tools/clean_tw_robots.py", date_str])
        print("\n[Очистка памяти после clean_tw]...", file=sys.stderr)
        gc.collect()
    else:
        print("\n[3/6] clean_tw_robots ПРОПУЩЕН (нет эталона TW)")

    # ---------- 4. tw_compare ----------
    tw_compare_out = ""
    if tw_file.exists():
        print("\n[4/6] tw_compare.py (сравнение с TW: метрики TP/FP/FN)...")
        rc, tw_compare_out = run_phase("tw_cmp", [sys.executable, "research/tw_compare.py", date_str])
        print("\n[Очистка памяти после tw_compare]...", file=sys.stderr)
        gc.collect()
    else:
        print("\n[4/6] tw_compare ПРОПУЩЕН (нет эталона TW)")

    # ---------- 5. tw_fn_summary ----------
    tw_fn_out = ""
    if tw_file.exists():
        print("\n[5/6] tw_fn_summary.py (кого мы режем на входе по min_qty)...")
        rc, tw_fn_out = run_phase("tw_fn", [sys.executable, "research/tw_fn_summary.py", date_str])
        print("\n[Очистка памяти после tw_fn_summary]...", file=sys.stderr)
        gc.collect()
    else:
        print("\n[5/6] tw_fn_summary ПРОПУЩЕН (нет эталона TW)")

    # ---------- E. сводка ----------
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
             f"Aniscan: {'есть' if aniscan.exists() else 'НЕТ (ждал '+str(waited)+'с)'} | "
             f"TW: {'есть' if tw_file.exists() else 'НЕТ'}")
    if missing:
        L.append("проблемы:")
        for m in missing:
            L.append("  " + m)
    if cov_lines:
        L.append("\n--- ПОКРЫТИЕ МОСТОМ ---")
        L.extend(cov_lines)

    L.append("\n--- РЕЗЮМЕ ДЛЯ ДОРАБОТКИ (из day_audit) ---")
    L.extend(summary)

    L.append("\n--- ВЫВОД day_audit (полный) ---")
    L.append(_clean_for_report(audit_out) if audit_out else "(не запускался)")

    L.append("\n--- ВЫВОД replay_alor (полный) ---")
    L.append(_clean_for_report(replay_out) if replay_out else "(не запускался)")

    L.append("\n--- ВЫВОД clean_tw_robots (дедуп TW) ---")
    L.append(_clean_for_report(tw_clean_out) if tw_clean_out else "(не запускался)")

    L.append("\n--- ВЫВОД tw_compare (сравнение с TW: TP/FP/FN) ---")
    L.append(_clean_for_report(tw_compare_out) if tw_compare_out else "(не запускался)")

    L.append("\n--- ВЫВОД tw_fn_summary (кого режем по min_qty) ---")
    L.append(_clean_for_report(tw_fn_out) if tw_fn_out else "(не запускался)")

    L.append("\n" + "=" * 78)
    text = "\n".join(L)
    rep.write_text(text, encoding="utf-8")

    print("\n" + "=" * 78)
    print(f" РИТУАЛ ЗАВЕРШЁН. Архив отчёта: {rep}")
    print(" Прогресс-бары и результаты уже выше в консоли — файл открывать не обязательно.")
    print("=" * 78)


if __name__ == "__main__":
    main()