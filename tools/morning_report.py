"""
tools/morning_report.py — утренний ритуал: один запуск, один отчёт.

Не дублирует day_audit/replay_alor, а ОРКЕСТРИРУЕТ их и добавляет своё:
  A. Инвентарь входов (лента моста / Алор / Aniscan / история) + что докачать.
  B. ПОКРЫТИЕ МОСТОМ: по каждому активному тикеру — есть ли его сделки в
     ленте моста за день (ответ «видит ли приблуда тикеры этого стакана»).
  C. replay_alor.py (истинные стороны) — с прогресс-баром.
  D. day_audit.py (матчинг v7 + классы FN + аудит FP) — с прогресс-баром,
     полный вывод кладётся в отчёт.
  E. Сводка «что чинить» на основе цифр.

Запуск: python tools/morning_report.py [YYYY-MM-DD]   # по умолчанию вчера
Отчёт:  output/morning_report_YYYY-MM-DD.txt

Прогресс-бары на всех фазах >5с (правило 2026-08-26).
"""
import sys
import json
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
    """Для subprocess без известного объёма: % времени + секунды + ETA по оценке."""
    def __init__(self, label, est_sec=60.0):
        self.label = label
        self.est = max(est_sec, 1.0)
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
        pct = min(99.0, 100.0 * el / self.est)
        filled = int(pct // 5)
        eta = max(0.0, self.est - el)
        line = (f"[{self.label}] [{'#' * filled}{'.' * (20 - filled)}] "
                f"{pct:3.0f}% | {el:.0f}с | ETA {eta:.0f}с {self.chars[self.i]}")
        if self.tty:
            sys.stdout.write("\r" + line)
            sys.stdout.flush()

    def done(self):
        el = time.time() - self.t0
        if self.tty:
            sys.stdout.write(f"\r[{self.label}] [{'#' * 20}] 100% | {el:.0f}с | ETA 0с" + " " * 8 + "\n")
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
    """B: покрытие мостом — есть ли сделки по каждому активному тикеру в ленте моста."""
    tape = DATA / f"pbsdk_trades_{date_str}.jsonl"
    if not tape.exists():
        return None
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
    return seen


def run_phase(label, cmd, est_sec):
    """C/D: запуск subprocess с прогресс-баром; возвращает (rc, stdout)."""
    sp = Spinner(label, est_sec)
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
    active = load_active_tickers()

    print(f"\n[входы] активных тикеров в settings: {len(active)}")
    print(f"[входы] лента моста   : {'есть' if tape_bridge.exists() else 'НЕТ'}")
    print(f"[входы] лента Алор    : {'есть' if tape_alor.exists() else 'НЕТ'}")
    print(f"[входы] эталон Aniscan: {'есть' if aniscan.exists() else 'НЕТ'}")

    missing = []
    if not aniscan.exists():
        missing.append(f"Aniscan за {date_str}:  python tools/aniscan_robot_history.py {date_str}"
                       f"  (браузер залогинен на aniscan.ru)")
    if not tape_alor.exists():
        missing.append(f"Алор за {date_str}:      python tools/alor_download_day.py {date_str}"
                       f"  (качается только законченный день)")
    if missing:
        print("\n[!] чего не хватает — выполни и повтори ритуал:")
        for m in missing:
            print("    " + m)

    # ---------- B. покрытие мостом ----------
    cov = bridge_coverage(date_str) if tape_bridge.exists() else None
    cov_lines = []
    if cov:
        no_data = sorted([s for s in active if s not in cov])
        cov_lines.append(f"покрытие мостом: {len(cov)}/{len(active)} тикеров дали сделки")
        if no_data:
            cov_lines.append(f"НЕ видно мостом ({len(no_data)}): {', '.join(no_data[:40])}"
                             + (" ..." if len(no_data) > 40 else ""))
        print("\n[покрытие] " + cov_lines[0])
        if len(cov_lines) > 1:
            print("[покрытие] " + cov_lines[1])

    # ---------- C. replay_alor ----------
    replay_out = ""
    if tape_alor.exists():
        print("\n[1/2] replay_alor.py (истинные стороны)...")
        rc, replay_out = run_phase("replay",
                                   [sys.executable, "research/replay_alor.py", date_str],
                                   est_sec=90)
        if rc != 0:
            print(f"[!] replay_alor rc={rc} — см. отчёт")
    else:
        print("\n[1/2] replay_alor ПРОПУЩЕН (нет ленты Алора)")

    # ---------- D. day_audit ----------
    audit_out = ""
    if aniscan.exists():
        print("\n[2/2] day_audit.py (матчинг v7 + классы FN + FP)...")
        rc, audit_out = run_phase("audit",
                                  [sys.executable, "research/day_audit.py", date_str],
                                  est_sec=120)
        if rc != 0:
            print(f"[!] day_audit rc={rc} — см. отчёт")
    else:
        print("\n[2/2] day_audit ПРОПУЩЕН (нет эталона Aniscan)")

    # ---------- E. сводка «что чинить» ----------
    summary = build_summary(audit_out, cov_lines)

    # ---------- сборка отчёта ----------
    OUT.mkdir(exist_ok=True)
    rep = OUT / f"morning_report_{date_str}.txt"
    L = []
    L.append("=" * 78)
    L.append(f" УТРЕННИЙ РИТУАЛ — {date_str}  (создан {datetime.now(MSK).strftime('%H:%M:%S')})")
    L.append("=" * 78)
    L.append("\n--- ВХОДЫ ---")
    L.append(f"активных тикеров: {len(active)}")
    L.append(f"лента моста: {'есть' if tape_bridge.exists() else 'НЕТ'} | "
             f"лента Алор: {'есть' if tape_alor.exists() else 'НЕТ'} | "
             f"Aniscan: {'есть' if aniscan.exists() else 'НЕТ'}")
    if missing:
        L.append("не хватает:")
        for m in missing:
            L.append("  " + m)
    if cov_lines:
        L.append("\n--- ПОКРЫТИЕ МОСТОМ ---")
        L.extend(cov_lines)
    L.append("\n--- РЕЗЮМЕ ДЛЯ ДОРАБОТКИ ---")
    L.extend(summary)
    L.append("\n--- ВЫВОД day_audit (полный) ---")
    L.append(audit_out if audit_out else "(не запускался)")
    L.append("\n--- ВЫВОД replay_alor (полный) ---")
    L.append(replay_out if replay_out else "(не запускался)")
    L.append("\n" + "=" * 78)
    text = "\n".join(L)
    rep.write_text(text, encoding="utf-8")

    print("\n" + "=" * 78)
    print(" РЕЗЮМЕ ДЛЯ ДОРАБОТКИ:")
    for s in summary:
        print("  " + s)
    print("=" * 78)
    print(f"[OK] отчёт: {rep}")


def build_summary(audit_out, cov_lines):
    """E: превращает цифры day_audit в приоритеты правок. Не выдумывает —
    парсит только то, что реально есть в выводе, остальное помечает как 'нужен разбор'."""
    out = []
    if cov_lines:
        out.append(cov_lines[0])
    if not audit_out.strip():
        out.append("нет вывода day_audit — метрики недоступны (нет эталона).")
        return out
    low = audit_out.lower()
    # ищем ключевые классы FN по подстрокам (формат day_audit v6)
    def grab(key):
        for ln in audit_out.splitlines():
            if key.lower() in ln.lower():
                return ln.strip()
        return None
    for key in ("GRID-IN-TAPE", "BURST-AGG", "IRREGULAR", "THIN", "NO-TRADES"):
        ln = grab(key)
        if ln:
            out.append(ln)
    # приоритет
    g = grab("GRID-IN-TAPE")
    if g and any(c.isdigit() for c in g):
        out.append(">>> ПРИОРИТЕТ: GRID-IN-TAPE > 0 = баг детектора (сетка в ленте есть, не поймали).")
    out.append(">>> FP/шум и длинные серии — смотреть блок FP в полном выводе ниже.")
    return out


if __name__ == "__main__":
    main()