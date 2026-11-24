import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
import re, uuid, time, base64, subprocess, winsound, msvcrt, pyperclip, requests
from pathlib import Path
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

GIGA_AUTH_KEY = "MDFhMWI5YTgtOTUyNS03MmU1LTgwYjctZGQ5ZGZhNDgzYWJmOjVkYzFlNTgxLTg2OTMtNDcxMy04ODJlLWQ5NzM3MGM2OWJjZA=="
GIGA_SCOPE = "GIGACHAT_API_PERS"
GIGA_MODEL = "GigaChat"
BASE_DIR = Path(__file__).parent
OUT_FILE = BASE_DIR / "runner_out.txt"
LOG_FILE = BASE_DIR / "runner_log.txt"
CMD_FILE = BASE_DIR / "_runner_cmd.ps1"
PYTHON_MARKERS = ("def ", "import ", "class ", "return ", "pyperclip", "subprocess")

def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def extract_local(message):
    blocks = list(re.finditer(r"```(?:powershell|cmd|bash)?\s*(.*?)\s*```", message, re.I | re.S))
    for m in reversed(blocks):
        block = m.group(1).strip()
        if not any(k in block for k in PYTHON_MARKERS):
            return block
    m = re.search(r"^CMD:\s*(.+)$", message, re.I | re.M)
    return m.group(1).strip() if m else None

def extract_giga(message):
    log("Блок не найден -> GigaChat...")
    try:
        r = requests.post("https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
                          headers={"Authorization": f"Basic {GIGA_AUTH_KEY}", "RqUID": str(uuid.uuid4()),
                                   "Content-Type": "application/x-www-form-urlencoded"},
                          data={"scope": GIGA_SCOPE}, verify=False, timeout=10)
        r.raise_for_status()
        token = r.json()["access_token"]
        r = requests.post("https://gigachat.devices.sberbank.ru/api/v1/chat/completions",
                          headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                          json={"model": GIGA_MODEL, "temperature": 0.1, "max_tokens": 500,
                                "messages": [
                                    {"role": "system", "content": "Извлеки команду для Windows-терминала. Верни ТОЛЬКО команду без markdown. Если команды нет: НЕТ_КОМАНДЫ."},
                                    {"role": "user", "content": message}]},
                          verify=False, timeout=30)
        r.raise_for_status()
        cmd = r.json()["choices"][0]["message"]["content"].strip()
        cmd = re.sub(r"^```(?:powershell|cmd|bash)?\s*", "", cmd, flags=re.I)
        cmd = re.sub(r"\s*```$", "", cmd, flags=re.I)
        log(f"GigaChat: {cmd[:80]}")
        return cmd.strip()
    except Exception as e:
        log(f"[ОШИБКА] GigaChat: {e}")
        return None

def run_command(command):
    log(f"Выполняю: {command[:100]}")
    try:
        ps_command = f"$env:PYTHONUTF8='1'; [console]::OutputEncoding = [System.Text.Encoding]::UTF8; {command}"
        encoded = base64.b64encode(ps_command.encode("utf-16-le")).decode("ascii")
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        out = r.stdout
        if r.stderr:
            out += f"\n[STDERR]:\n{r.stderr}"
        if not out.strip():
            out = f"Выполнено (код {r.returncode}), вывода нет."
        log(f"Готово, код {r.returncode}, {len(out)} симв.")
        return out
    except subprocess.TimeoutExpired:
        return "[ОШИБКА] Таймаут 120 сек."
    except Exception as e:
        return f"[ОШИБКА] {e}"

def is_recursion(cmd):
    low = cmd.lower()
    return "runner.py" in low or ("runner_out" in low and "copy" in low)

def beep():
    try:
        winsound.Beep(1000, 200)
        winsound.Beep(1200, 300)
    except Exception:
        pass

def drain():
    while msvcrt.kbhit():
        msvcrt.getch()

def main():
    try:
        LOG_FILE.write_text("", encoding="utf-8")
    except Exception:
        pass
    log("=" * 60)
    log("[РАННЕР] Запущен. Скопируй сообщение -> вставь сюда -> Enter.")
    log("=" * 60)
    while True:
        try:
            input("\n[ЖДУ] Вставь сообщение и нажми Enter> ")
        except (EOFError, KeyboardInterrupt):
            log("[РАННЕР] Остановлен.")
            break
        drain()
        try:
            message = pyperclip.paste()
        except Exception as e:
            log(f"[ОШИБКА] буфер: {e}")
            continue
        if not message or not message.strip():
            log("Буфер пуст.")
            continue
        log(f"Буфер: {len(message)} симв.")
        cmd = extract_local(message)
        src = "локально"
        if not cmd:
            cmd = extract_giga(message)
            src = "GigaChat"
        if not cmd or cmd == "НЕТ_КОМАНДЫ":
            result = "Команда не извлечена (нет блока кода / команды)."
        elif is_recursion(cmd):
            result = f"[ЗАЩИТА] Рекурсия: {cmd[:80]}"
        else:
            log(f"Команда ({src}): {cmd[:80]}")
            result = run_command(cmd)
        OUT_FILE.write_text(result, encoding="utf-8")
        log(f"runner_out.txt: {len(result)} симв.")
        try:
            pyperclip.copy(result)
            log("Результат в буфере.")
        except Exception as e:
            log(f"[ОШИБКА] буфер: {e}")
        beep()
        log("[ГОТОВО] Ctrl+V куда надо.")

if __name__ == "__main__":
    main()