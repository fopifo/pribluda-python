"""
tools/system_health_check.py — проверка работоспособности компонентов start_all.bat.
"""
import json
import urllib.request
import urllib.error
import subprocess
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
TODAY = datetime.now().strftime("%Y-%m-%d")


def print_status(name, is_ok, details=""):
    status = "✅ OK" if is_ok else "❌ FAIL"
    color = "\033[92m" if is_ok else "\033[91m"
    reset = "\033[0m"
    print(f"{color}[{status}]{reset} {name:<35} {details}")


def check_tw_server():
    """GET вместо HEAD: SimpleHTTPRequestHandler не поддерживает HEAD.
    Любой HTTP-ответ (200/404) = порт слушает = сервер жив."""
    try:
        req = urllib.request.Request("http://127.0.0.1:8765/", method="GET")
        with urllib.request.urlopen(req, timeout=3) as response:
            return True, f"Сервер отвечает (HTTP {response.status})"
    except urllib.error.HTTPError as e:
        return True, f"Сервер отвечает (HTTP {e.code})"
    except Exception as e:
        return False, f"Недоступен: {e}"


def check_bridge_state():
    f = DATA / "bridge_state.json"
    if not f.exists():
        return False, "Файл не найден"
    mtime = datetime.fromtimestamp(f.stat().st_mtime)
    age = (datetime.now() - mtime).total_seconds()
    if age < 60:
        return True, f"Обновлён {int(age)} сек. назад"
    return False, f"Устарел ({int(age)} сек.)"


def check_file_exists(pattern, min_size=50):
    files = list(DATA.glob(pattern))
    if not files:
        return False, "Файл не найден"
    latest = max(files, key=lambda p: p.stat().st_mtime)
    size = latest.stat().st_size
    if size >= min_size:
        return True, f"{latest.name} ({size/1024:.1f} КБ)"
    return False, f"{latest.name} слишком мал ({size} байт)"


def check_process_running(process_name):
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}"],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        return process_name.lower() in result.stdout.lower(), f"Процесс {process_name} активен"
    except Exception:
        return False, "Ошибка проверки процессов"


def main():
    print("=" * 70)
    print(" ПРОВЕРКА СИСТЕМЫ (start_all.bat components)")
    print("=" * 70)

    ok, msg = check_tw_server()
    print_status("1. TW Server (порт 8765)", ok, msg)

    ok, msg = check_bridge_state()
    print_status("2. PbSdk Bridge (мост)", ok, msg)

    ok, msg = check_file_exists(f"tw_robots_{TODAY}.jsonl")
    print_status("3. Расширение T-Widgets", ok, msg)

    ok, msg = check_file_exists(f"aniscan_robots_{TODAY}.jsonl")
    print_status("4. Расширение Aniscan", ok, msg)

    ok, msg = check_file_exists("positions_*.json")
    print_status("5. Snapshot Positions", ok, msg)

    ok, msg = check_process_running("python.exe")
    print_status("6. Python процессы (main/bot)", ok, msg)

    ok, msg = check_process_running("PbSdkConsole.exe")
    print_status("7. PbSdk Console (.NET)", ok, msg)

    print("=" * 70)
    print(" РУЧНЫЕ ПРОВЕРКИ:")
    print("  • /balance в боте — сверь пункты с терминалом MOEX")
    print("  • Главное окно Приблуды (main.py) — нет ли красных ошибок")
    print("  • F12 на aniscan.ru — нет ли ошибок расширения")
    print("=" * 70)


if __name__ == "__main__":
    main()