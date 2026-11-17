"""
tools/morning_report.py — автоматический утренний аудит за вчерашний день.
1. Определяет вчерашнюю дату.
2. Проверяет наличие эталона Aniscan за эту дату.
3. Запускает replay_alor.py (если истории нет).
4. Запускает day_audit.py и сохраняет чистый отчёт в output/morning_report_<дата>.txt.
Прогресс-бар встроен (правило >5с).
"""
import sys
import time
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
MSK = ZoneInfo("Europe/Moscow")
OUT_DIR = BASE / "output"
DATA_DIR = BASE / "data"

class Progress:
    def __init__(self, label):
        self.label = label
        self.t0 = time.time()
        self.last = 0.0
        self.chars = "|/-\\"
        self.idx = 0

    def tick(self):
        now = time.time()
        if now - self.last < 0.2:
            return
        self.last = now
        self.idx = (self.idx + 1) % 4
        el = now - self.t0
        sys.stdout.write(f"\r[{self.label}] {self.chars[self.idx]} {el:.1f}с")
        sys.stdout.flush()

    def done(self):
        el = time.time() - self.t0
        sys.stdout.write(f"\r[{self.label}] ✅ готово за {el:.1f}с" + " " * 20 + "\n")
        sys.stdout.flush()

def main():
    yesterday = (datetime.now(MSK) - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"[*] Утренний отчёт за: {yesterday}")

    aniscan_file = DATA_DIR / f"aniscan_robots_{yesterday}.jsonl"
    if not aniscan_file.exists():
        print(f"[!] ВНИМАНИЕ: нет файла {aniscan_file.name}")
        print("    Сначала скачай эталон: python tools/aniscan_robot_history.py {yesterday}")
        print("    (убедись, что браузер залогинен на aniscan.ru)")
        sys.exit(1)

    hist_file = DATA_DIR / "replay_alor_history.jsonl"
    # Если файл есть, но он старый (не за вчерашний день), лучше перезапустить
    # Но для простоты утреннего отчёта мы просто запускаем replay_alor с флагом,
    # который гарантирует свежий прогон.
    
    print("[*] Запуск replay_alor.py...")
    p = Progress("replay")
    cmd = [sys.executable, "research/replay_alor.py", yesterday]
    try:
        # Запускаем в фоне, но с перенаправлением вывода, чтобы не засорять консоль
        # Для прогресс-бара будем просто тикать, пока процесс идёт
        proc = subprocess.Popen(
            cmd, cwd=str(BASE), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace"
        )
        while proc.poll() is None:
            p.tick()
            time.sleep(0.2)
        stdout, stderr = proc.communicate()
        p.done()
        if proc.returncode != 0:
            print(f"[!] Ошибка replay_alor: {stderr}")
            sys.exit(1)
    except Exception as e:
        print(f"[!] Ошибка запуска: {e}")
        sys.exit(1)

    print("[*] Запуск day_audit.py...")
    p2 = Progress("audit")
    cmd_audit = [sys.executable, "research/day_audit.py", yesterday]
    try:
        proc2 = subprocess.Popen(
            cmd_audit, cwd=str(BASE), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace"
        )
        while proc2.poll() is None:
            p2.tick()
            time.sleep(0.2)
        stdout, stderr = proc2.communicate()
        p2.done()
        if proc2.returncode != 0:
            print(f"[!] Ошибка day_audit: {stderr}")
            sys.exit(1)
    except Exception as e:
        print(f"[!] Ошибка запуска: {e}")
        sys.exit(1)

    # Сохраняем отчёт
    OUT_DIR.mkdir(exist_ok=True)
    report_path = OUT_DIR / f"morning_report_{yesterday}.txt"
    report_path.write_text(stdout, encoding="utf-8")
    
    print("\n" + "=" * 60)
    print("✅ УТРЕННИЙ ОТЧЁТ ГОТОВ")
    print("=" * 60)
    print(f"Файл: {report_path}")
    print("\nКраткая сводка (первые 15 строк):")
    lines = stdout.split("\n")
    for line in lines[:15]:
        if line.strip():
            print("  " + line)
    print("  ...")
    print(f"Полный текст открой в: notepad {report_path}")

if __name__ == "__main__":
    main()